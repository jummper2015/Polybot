"""
Tests for MarketService discovery filter — slug-authoritative window matching.

Bug fixed (2026-06-22, dashboard fix):
  Polymarket live crypto markets use slug `*-updown-{5m|15m}-*`. The old
  `_matches_window` fallback (`_matches_live_crypto_window`) accepted any
  live crypto market as M5, which mis-labelled `eth-updown-15m-*` as M5.
  Combined with `save_market` upsert by condition_id, this collapsed
  4 distinct (asset, window) pairs into 2-3 rows in the dashboard.

These tests pin the new contract:
  - Slug `-5m-` → window MUST be M5. M15 matches MUST return False.
  - Slug `-15m-` → window MUST be M15. M5 matches MUST return False.
  - Discovery of 4 fixture markets (BTC/ETH × M5/M15) yields exactly 4
    parsed entries, one per (asset, window).
  - When two raw markets share the same condition_id but distinct slugs,
    each is correctly classified by its slug.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.application.services.market_service import MarketService
from src.domain.enums.asset import Asset
from src.domain.enums.window import Window


@pytest.fixture
def svc() -> MarketService:
    return MarketService(
        market_data_port=MagicMock(),
        repository=MagicMock(),
        redis=MagicMock(),
    )


def _make_raw(
    slug: str,
    question: str,
    *,
    condition_id: str = "0x" + "a" * 64,
    yes_token: str = "1",
    no_token: str = "2",
    volume: float = 100.0,
) -> dict:
    """Builds a minimal dict in the shape MarketService._filter_and_parse expects."""
    return {
        "condition_id": condition_id,
        "question":     question,
        "slug":         slug,
        "active":       True,
        "tokens": [
            {"outcome": "Up",   "token_id": yes_token, "price": 0.5},
            {"outcome": "Down", "token_id": no_token,  "price": 0.5},
        ],
        "volume24hr":      volume,
        "start_date_iso":  "",
        "end_date_iso":    "",
    }


class TestMatchesWindowSlugWins:
    """Slug is authoritative — never crosses the M5/M15 boundary."""

    def test_slug_5m_matches_m5_only(self, svc: MarketService) -> None:
        raw = _make_raw(
            slug="btc-updown-5m-1782134100",
            question="Bitcoin Up or Down - June 22, 1:20PM-1:25PM ET",
        )
        assert svc._matches_window(raw, Window.M5) is True
        assert svc._matches_window(raw, Window.M15) is False

    def test_slug_15m_matches_m15_only(self, svc: MarketService) -> None:
        raw = _make_raw(
            slug="eth-updown-15m-1782181800",
            question="Ethereum Up or Down - June 22, 10:30PM-10:45PM ET",
        )
        assert svc._matches_window(raw, Window.M15) is True
        assert svc._matches_window(raw, Window.M5) is False

    def test_slug_15m_with_partial_date_iso_still_blocks_m5(
        self, svc: MarketService
    ) -> None:
        """Regression: the bug was here. Slug -15m- + start_date_iso='2026-06-22'
        (date-only, no time) used to fall through to _matches_live_crypto_window
        which accepted any live crypto as M5."""
        raw = _make_raw(
            slug="eth-updown-15m-1782181800",
            question="Ethereum Up or Down on June 22, 2026",
        )
        raw["start_date_iso"] = "2026-06-22"
        raw["end_date_iso"]   = "2026-06-23"
        assert svc._matches_window(raw, Window.M5) is False
        assert svc._matches_window(raw, Window.M15) is True

    def test_15_minute_alt_slug_pattern_recognized(self, svc: MarketService) -> None:
        raw = _make_raw(
            slug="bitcoin-price-15-minute-window-jun22",
            question="Bitcoin price in next 15-minute window",
        )
        assert svc._matches_window(raw, Window.M15) is True
        assert svc._matches_window(raw, Window.M5) is False


class TestMatchesLiveCryptoWindowDefenseInDepth:
    """The inner helper also guards against window crossover."""

    def test_m5_branch_rejects_15m_slug(self, svc: MarketService) -> None:
        raw = _make_raw(
            slug="btc-updown-15m-1782149400",
            question="Bitcoin Up or Down on June 22",
        )
        assert svc._matches_live_crypto_window(raw, Window.M5) is False
        assert svc._matches_live_crypto_window(raw, Window.M15) is True

    def test_m15_branch_rejects_5m_slug(self, svc: MarketService) -> None:
        raw = _make_raw(
            slug="btc-updown-5m-1782134100",
            question="Bitcoin Up or Down on June 22",
        )
        assert svc._matches_live_crypto_window(raw, Window.M5) is True
        assert svc._matches_live_crypto_window(raw, Window.M15) is False

    def test_non_live_crypto_rejected(self, svc: MarketService) -> None:
        raw = _make_raw(
            slug="random-political-market",
            question="Will candidate X win the election?",
        )
        assert svc._matches_live_crypto_window(raw, Window.M5) is False
        assert svc._matches_live_crypto_window(raw, Window.M15) is False


class TestDiscoveryProducesFourPairs:
    """End-to-end (in-memory): four fixture markets → exactly 4 (asset, window) outputs."""

    def test_one_market_per_pair(self, svc: MarketService) -> None:
        raw_markets = [
            _make_raw(
                slug="btc-updown-5m-1782134100",
                question="Bitcoin Up or Down - 1:20PM-1:25PM ET",
                condition_id="0x" + "1" * 64,
                yes_token="11", no_token="12",
                volume=5.0,
            ),
            _make_raw(
                slug="btc-updown-15m-1782145800",
                question="Bitcoin Up or Down - 5:30PM-5:45PM ET",
                condition_id="0x" + "2" * 64,
                yes_token="21", no_token="22",
                volume=3.0,
            ),
            _make_raw(
                slug="eth-updown-5m-1782182400",
                question="Ethereum Up or Down - 10:40PM-10:45PM ET",
                condition_id="0x" + "3" * 64,
                yes_token="31", no_token="32",
                volume=4.0,
            ),
            _make_raw(
                slug="eth-updown-15m-1782181800",
                question="Ethereum Up or Down - 10:30PM-10:45PM ET",
                condition_id="0x" + "4" * 64,
                yes_token="41", no_token="42",
                volume=2.0,
            ),
        ]

        results = {
            (asset, window): svc._filter_and_parse(raw_markets, asset, window)
            for asset in Asset
            for window in Window
        }

        for (asset, window), markets in results.items():
            assert len(markets) == 1, (
                f"expected exactly 1 market for ({asset.value}, {window.value}), "
                f"got {len(markets)}"
            )
            m = markets[0]
            assert m.asset == asset
            assert m.window == window

        # All 4 condition_ids must be distinct (no upsert collision).
        ids = {markets[0].id for markets in results.values()}
        assert len(ids) == 4

    def test_dedup_picks_highest_volume_within_pair(
        self, svc: MarketService
    ) -> None:
        """When multiple BTC-5m markets exist, top-volume wins."""
        raw_markets = [
            _make_raw(
                slug="btc-updown-5m-1782134100",
                question="Bitcoin Up or Down - 1:20PM-1:25PM ET",
                condition_id="0x" + "1" * 64,
                volume=1.0,
            ),
            _make_raw(
                slug="btc-updown-5m-1782134400",
                question="Bitcoin Up or Down - 1:25PM-1:30PM ET",
                condition_id="0x" + "2" * 64,
                volume=10.0,  # winner
            ),
            _make_raw(
                slug="btc-updown-5m-1782135000",
                question="Bitcoin Up or Down - 1:35PM-1:40PM ET",
                condition_id="0x" + "3" * 64,
                volume=5.0,
            ),
        ]
        markets = svc._filter_and_parse(raw_markets, Asset.BTC, Window.M5)
        assert len(markets) == 1
        assert markets[0].id == "0x" + "2" * 64
        assert markets[0].volume_24h == 10.0


class TestNoCrossClassification:
    """A raw market never classifies into both M5 and M15."""

    @pytest.mark.parametrize(
        "slug, expected_window",
        [
            ("btc-updown-5m-1782134100",  Window.M5),
            ("btc-updown-15m-1782145800", Window.M15),
            ("eth-updown-5m-1782182400",  Window.M5),
            ("eth-updown-15m-1782181800", Window.M15),
        ],
    )
    def test_slug_yields_single_window(
        self, svc: MarketService, slug: str, expected_window: Window
    ) -> None:
        raw = _make_raw(slug=slug, question=f"crypto market {slug}")
        m5_match  = svc._matches_window(raw, Window.M5)
        m15_match = svc._matches_window(raw, Window.M15)
        if expected_window == Window.M5:
            assert m5_match and not m15_match
        else:
            assert m15_match and not m5_match
