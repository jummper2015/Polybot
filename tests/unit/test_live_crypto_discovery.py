"""
Tests for B5 fix: Polymarket live crypto market discovery.

Polymarket publishes live crypto markets every 5 or 15 minutes using the
"Up or Down" / "Price" naming pattern, NOT the "-5m-" slug pattern. The
previous discovery code only matched the latter, missing all live crypto
markets. These tests verify the new matchers and rotation logic.
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.record_live_data import (
    _detect_asset,
    _is_live_crypto_market,
    _matches_window,
    find_live_crypto_markets,
    find_markets_for_asset,
    select_next_market_for_rotation,
)
from src.application.services.market_service import (
    LIVE_PRICE_ET_PATTERN,
    LIVE_UP_DOWN_CRYPTO_PATTERN,
    MarketService,
    _is_live_crypto_market as _ms_is_live_crypto,
)
from src.domain.enums.window import Window


def _build_market(
    *,
    condition_id: str,
    slug: str,
    title: str,
    end_date: str,
    volume24hr: float = 0.0,
) -> dict:
    """Build a raw market dict in the camelCase shape returned by
    ``/events/keyset`` (matches Polymarket Gamma actual response).

    ``outcomes`` / ``outcomePrices`` / ``clobTokenIds`` are JSON-encoded
    strings just like the real API. ``PolymarketAdapter.parse_rest_market``
    unwraps them.
    """
    return {
        "conditionId": condition_id,
        "slug": slug,
        "question": title,
        "title": title,
        "active": True,
        "closed": False,
        "endDate": end_date,
        "volume24hr": volume24hr,
        "outcomes": _json.dumps(["Up", "Down"]),
        "outcomePrices": _json.dumps(["0.5", "0.5"]),
        "clobTokenIds": _json.dumps(["111111", "222222"]),
    }


def _mock_keyset_response(events: list[dict], next_cursor: str | None = None):
    """Build the ``{"events": [...], "next_cursor": ...}`` payload returned
    by ``/events/keyset``."""
    return {"events": events, "next_cursor": next_cursor}


def _patch_httpx_with(payload):
    """Helper to mount the standard async-context-manager mock chain.

    Returns the ``patch`` context manager — caller uses ``with`` over it.
    """
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = payload
    mock_response.raise_for_status = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    p = patch("scripts.record_live_data.httpx.AsyncClient")
    return p, mock_client


# ──────────────────────────────────────────────────────────────────
# _detect_asset
# ──────────────────────────────────────────────────────────────────


class TestDetectAsset:
    """Detects BTC vs ETH avoiding false positives like 'Ethiopia'."""

    def test_detects_btc_from_question(self):
        raw = {"question": "Bitcoin Up or Down on June 14", "slug": "btc-up-or-down"}
        assert _detect_asset(raw) == "BTC"

    def test_detects_eth_from_question(self):
        raw = {"question": "Ethereum Up or Down on June 14", "slug": "eth-up-or-down"}
        assert _detect_asset(raw) == "ETH"

    def test_lowercase_btc(self):
        raw = {"question": "bitcoin price target", "slug": ""}
        assert _detect_asset(raw) == "BTC"

    def test_lowercase_eth(self):
        raw = {"question": "ethereum price target", "slug": ""}
        assert _detect_asset(raw) == "ETH"

    def test_no_asset_returns_none(self):
        raw = {"question": "Will it rain tomorrow?", "slug": "weather"}
        assert _detect_asset(raw) is None

    def test_empty_returns_none(self):
        assert _detect_asset({}) is None
        assert _detect_asset({"question": "", "slug": ""}) is None

    def test_prefers_first_appearance_on_ambiguity(self):
        """If both BTC and ETH appear, return whichever appears first."""
        raw = {
            "question": "Bitcoin and Ethereum both pump?",
            "slug": "btc-eth",
        }
        result = _detect_asset(raw)
        assert result in ("BTC", "ETH")  # Whichever appears first

    def test_uses_title_or_question_fallback(self):
        """Some events use 'title' instead of 'question'."""
        raw = {"title": "Bitcoin Up or Down on June 14", "slug": ""}
        assert _detect_asset(raw) == "BTC"


# ──────────────────────────────────────────────────────────────────
# _is_live_crypto_market
# ──────────────────────────────────────────────────────────────────


class TestIsLiveCryptoMarket:
    """Detects Polymarket live crypto Up/Down and Price markets."""

    def test_bitcoin_up_or_down(self):
        raw = {
            "title": "Bitcoin Up or Down on June 14, 3:35PM ET",
            "slug": "bitcoin-up-or-down-on-june-14-335pm-et",
        }
        is_live, window, asset = _is_live_crypto_market(raw)
        assert is_live is True
        assert asset == "BTC"
        assert window in ("5m", "15m")

    def test_ethereum_up_or_down(self):
        raw = {
            "title": "Ethereum Up or Down on June 14, 3:35PM ET",
            "slug": "ethereum-up-or-down-on-june-14-335pm-et",
        }
        is_live, window, asset = _is_live_crypto_market(raw)
        assert is_live is True
        assert asset == "ETH"

    def test_bitcoin_price_with_et(self):
        raw = {
            "title": "Bitcoin Price - June 14 5:00PM ET",
            "slug": "bitcoin-price-june-14-5pm-et",
        }
        is_live, _w, asset = _is_live_crypto_market(raw)
        assert is_live is True
        assert asset == "BTC"

    def test_ethereum_price_with_et(self):
        raw = {
            "title": "Ethereum Price - June 14 5:00PM ET",
            "slug": "ethereum-price-june-14-5pm-et",
        }
        is_live, _w, asset = _is_live_crypto_market(raw)
        assert is_live is True
        assert asset == "ETH"

    def test_longevous_btc_market_rejected(self):
        """Markets like 'Will bitcoin hit $1m before GTA VI?' are NOT live."""
        raw = {
            "title": "Will bitcoin hit $1m before GTA VI?",
            "slug": "will-bitcoin-hit-1m-before-gta-vi-872-424",
        }
        is_live, _, _ = _is_live_crypto_market(raw)
        assert is_live is False

    def test_longevous_eth_market_rejected(self):
        raw = {
            "title": "Will MegaETH perform an airdrop by June 30?",
            "slug": "will-megaeth-perform-an-airdrop-by-june-30",
        }
        is_live, _, _ = _is_live_crypto_market(raw)
        assert is_live is False

    def test_unrelated_market_rejected(self):
        raw = {
            "title": "Will the Fed cut rates in June?",
            "slug": "fed-cuts-rates-june",
        }
        is_live, _, _ = _is_live_crypto_market(raw)
        assert is_live is False

    def test_ethiopia_false_positive_prevented(self):
        """'Ethiopia' contains 'eth' — must not be detected as Ethereum."""
        raw = {
            "title": "Next Prime Minister of Ethiopia?",
            "slug": "next-prime-minister-ethiopia",
        }
        is_live, _, _ = _is_live_crypto_market(raw)
        assert is_live is False

    def test_question_fallback(self):
        """Some endpoints use 'question' instead of 'title'."""
        raw = {
            "question": "Bitcoin Up or Down on June 14",
            "slug": "bitcoin-up-or-down",
        }
        is_live, _w, asset = _is_live_crypto_market(raw)
        assert is_live is True
        assert asset == "BTC"

    def test_explicit_5m_in_slug(self):
        raw = {
            "title": "Bitcoin Up or Down on June 14",
            "slug": "bitcoin-up-or-down-5m-june-14",
        }
        is_live, window, _ = _is_live_crypto_market(raw)
        assert is_live is True
        assert window == "5m"

    def test_explicit_15m_in_slug(self):
        raw = {
            "title": "Ethereum Up or Down on June 14",
            "slug": "ethereum-up-or-down-15m-june-14",
        }
        is_live, window, _ = _is_live_crypto_market(raw)
        assert is_live is True
        assert window == "15m"


# ──────────────────────────────────────────────────────────────────
# _matches_window (record_live_data version)
# ──────────────────────────────────────────────────────────────────


class TestMatchesWindowLiveCrypto:
    """Verifies _matches_window now accepts the Up/Down pattern."""

    def test_5m_accepts_bitcoin_up_or_down(self):
        raw = {
            "title": "Bitcoin Up or Down on June 14, 3:35PM ET",
            "slug": "bitcoin-up-or-down-on-june-14-335pm-et",
        }
        assert _matches_window(raw, window="5m") is True

    def test_15m_accepts_ethereum_with_15m_slug(self):
        raw = {
            "title": "Ethereum Up or Down on June 14",
            "slug": "ethereum-up-or-down-15m-june-14",
        }
        assert _matches_window(raw, window="15m") is True

    def test_legacy_5m_slug_still_works(self):
        raw = {
            "title": "Will BTC go up in 5 min?",
            "slug": "btc-up-5m-june-14",
        }
        assert _matches_window(raw, window="5m") is True

    def test_legacy_15m_slug_still_works(self):
        raw = {
            "title": "Will ETH go up in 15 min?",
            "slug": "eth-up-15m-june-14",
        }
        assert _matches_window(raw, window="15m") is True

    def test_longevous_market_rejected(self):
        raw = {
            "title": "Will bitcoin hit $1m before GTA VI?",
            "slug": "will-bitcoin-hit-1m-before-gta-vi-872-424",
        }
        assert _matches_window(raw, window="5m") is False
        assert _matches_window(raw, window="15m") is False

    def test_time_range_pattern_still_works(self):
        raw = {
            "title": "Will BTC be above 100k from 9:30AM-9:35AM?",
            "slug": "btc-9-30-9-35",
        }
        assert _matches_window(raw, window="5m") is True


# ──────────────────────────────────────────────────────────────────
# find_live_crypto_markets (async)
# ──────────────────────────────────────────────────────────────────


class TestFindLiveCryptoMarkets:
    """Verifies async discovery returns markets sorted by endDate.

    Post-B5-recheck (2026-06-18): the script now calls ``/events/keyset?tag=crypto``
    instead of ``/markets?_limit=500``. Mocks use the keyset response shape
    ``{"events": [{..., "markets": [...]}], "next_cursor": null}`` to match
    what ``PolymarketHTTPClient.get_active_markets`` already consumes.
    """

    @pytest.mark.asyncio
    async def test_filters_by_asset_btc(self):
        now = datetime.now(timezone.utc)
        events = [
            {
                "id": "ev1",
                "markets": [
                    _build_market(
                        condition_id="0x1",
                        slug="bitcoin-up-or-down-1",
                        title="Bitcoin Up or Down on June 14, 3:35PM ET",
                        end_date=(now + timedelta(minutes=2)).isoformat(),
                    ),
                    _build_market(
                        condition_id="0x2",
                        slug="ethereum-up-or-down-1",
                        title="Ethereum Up or Down on June 14",
                        end_date=(now + timedelta(minutes=5)).isoformat(),
                    ),
                ],
            },
        ]
        p, _client = _patch_httpx_with(_mock_keyset_response(events))
        with p as mock_cls:
            mock_cls.return_value = _client

            result = await find_live_crypto_markets("BTC")

        assert len(result) == 1
        assert result[0]["conditionId"] == "0x1"

    @pytest.mark.asyncio
    async def test_sorts_by_enddate_ascending(self):
        now = datetime.now(timezone.utc)
        events = [
            {
                "id": "ev1",
                "markets": [
                    _build_market(
                        condition_id="0x_late",
                        slug="btc-late",
                        title="Bitcoin Up or Down on June 14",
                        end_date=(now + timedelta(minutes=10)).isoformat(),
                    ),
                    _build_market(
                        condition_id="0x_early",
                        slug="btc-early",
                        title="Bitcoin Up or Down on June 14",
                        end_date=(now + timedelta(minutes=2)).isoformat(),
                    ),
                ],
            },
        ]
        p, _client = _patch_httpx_with(_mock_keyset_response(events))
        with p as mock_cls:
            mock_cls.return_value = _client

            result = await find_live_crypto_markets("BTC")

        assert [m["conditionId"] for m in result] == ["0x_early", "0x_late"]


# ──────────────────────────────────────────────────────────────────
# find_markets_for_asset (async) — B5 re-check
# ──────────────────────────────────────────────────────────────────


class TestFindMarketsForAsset:
    """Regression coverage for the B5 fix: discovery must hit
    ``/events/keyset?tag=crypto`` (which exposes ``*-updown-*`` markets)
    and reject longevity-style markets like the historic GTA-VI binary.
    """

    @pytest.mark.asyncio
    async def test_finds_updown_5m_rejects_longevity_market(self):
        now = datetime.now(timezone.utc)
        events = [
            {
                "id": "ev_updown",
                "markets": [
                    _build_market(
                        condition_id="0x_updown",
                        slug="btc-updown-5m-1781817300",
                        title="Bitcoin Up or Down - June 18, 5:15PM-5:20PM ET",
                        end_date=(now + timedelta(minutes=5)).isoformat(),
                        volume24hr=149.86,
                    ),
                ],
            },
            {
                "id": "ev_gta",
                "markets": [
                    _build_market(
                        condition_id="0x_gta",
                        slug="will-bitcoin-hit-1m-before-gta-vi",
                        title="Will bitcoin hit $1m before GTA VI?",
                        end_date="2030-01-01T00:00:00Z",
                        volume24hr=50000.0,
                    ),
                ],
            },
        ]
        p, _client = _patch_httpx_with(_mock_keyset_response(events))
        with p as mock_cls:
            mock_cls.return_value = _client

            result = await find_markets_for_asset("BTC", windows=("5m", "15m"))

        slugs = [m["slug"] for m in result]
        assert "btc-updown-5m-1781817300" in slugs
        assert "will-bitcoin-hit-1m-before-gta-vi" not in slugs

    @pytest.mark.asyncio
    async def test_paginates_via_next_cursor_and_aggregates(self):
        now = datetime.now(timezone.utc)
        page1 = _mock_keyset_response(
            [
                {
                    "id": "ev1",
                    "markets": [
                        _build_market(
                            condition_id="0x_a",
                            slug="btc-updown-5m-1",
                            title="Bitcoin Up or Down",
                            end_date=(now + timedelta(minutes=3)).isoformat(),
                            volume24hr=100.0,
                        ),
                    ],
                }
            ],
            next_cursor="cursor_p2",
        )
        page2 = _mock_keyset_response(
            [
                {
                    "id": "ev2",
                    "markets": [
                        _build_market(
                            condition_id="0x_b",
                            slug="btc-updown-15m-2",
                            title="Bitcoin Up or Down 15m",
                            end_date=(now + timedelta(minutes=15)).isoformat(),
                            volume24hr=50.0,
                        ),
                    ],
                }
            ],
            next_cursor=None,
        )

        mock_client = AsyncMock()
        first = MagicMock()
        first.json.return_value = page1
        first.raise_for_status = MagicMock()
        second = MagicMock()
        second.json.return_value = page2
        second.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(side_effect=[first, second])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("scripts.record_live_data.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            result = await find_markets_for_asset("BTC", windows=("5m", "15m"))

        assert mock_client.get.await_count == 2
        slugs = {m["slug"] for m in result}
        assert slugs == {"btc-updown-5m-1", "btc-updown-15m-2"}

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty_list(self):
        p, _client = _patch_httpx_with(_mock_keyset_response([]))
        with p as mock_cls:
            mock_cls.return_value = _client
            result = await find_markets_for_asset("BTC")
        assert result == []

    @pytest.mark.asyncio
    async def test_keeps_only_top_volume_per_window(self):
        now = datetime.now(timezone.utc)
        events = [
            {
                "id": "ev1",
                "markets": [
                    _build_market(
                        condition_id=f"0x_{i}",
                        slug=f"btc-updown-5m-{i}",
                        title="Bitcoin Up or Down",
                        end_date=(now + timedelta(minutes=3 + i)).isoformat(),
                        volume24hr=float(100 - i),
                    )
                    for i in range(8)
                ],
            }
        ]
        p, _client = _patch_httpx_with(_mock_keyset_response(events))
        with p as mock_cls:
            mock_cls.return_value = _client
            result = await find_markets_for_asset("BTC", windows=("5m",), max_per_window=3)
        assert len(result) == 3
        # Higher volumes first.
        vols = [m["volume24hr"] for m in result]
        assert vols == sorted(vols, reverse=True)


# ──────────────────────────────────────────────────────────────────
# select_next_market_for_rotation
# ──────────────────────────────────────────────────────────────────


class TestSelectNextMarketForRotation:
    """Verifies the auto-rotation logic between consecutive markets."""

    def test_no_markets_returns_none(self):
        assert select_next_market_for_rotation([], None) is None

    def test_no_current_returns_first_non_expired(self):
        now = datetime.now(timezone.utc)
        markets = [
            {"conditionId": "0x_a", "endDate": (now - timedelta(minutes=5)).isoformat()},  # expired
            {"conditionId": "0x_b", "endDate": (now + timedelta(minutes=2)).isoformat()},  # live
        ]
        result = select_next_market_for_rotation(markets, None)
        assert result["conditionId"] == "0x_b"

    def test_current_still_valid_is_kept(self):
        now = datetime.now(timezone.utc)
        markets = [
            {"conditionId": "0x_a", "endDate": (now + timedelta(minutes=2)).isoformat()},
            {"conditionId": "0x_b", "endDate": (now + timedelta(minutes=10)).isoformat()},
        ]
        result = select_next_market_for_rotation(markets, "0x_a")
        assert result["conditionId"] == "0x_a"

    def test_current_expired_advances_to_next(self):
        now = datetime.now(timezone.utc)
        markets = [
            {"conditionId": "0x_a", "endDate": (now - timedelta(seconds=1)).isoformat()},  # just expired
            {"conditionId": "0x_b", "endDate": (now + timedelta(minutes=10)).isoformat()},
        ]
        result = select_next_market_for_rotation(markets, "0x_a")
        assert result["conditionId"] == "0x_b"

    def test_all_expired_returns_none(self):
        now = datetime.now(timezone.utc)
        markets = [
            {"conditionId": "0x_a", "endDate": (now - timedelta(minutes=5)).isoformat()},
            {"conditionId": "0x_b", "endDate": (now - timedelta(minutes=10)).isoformat()},
        ]
        assert select_next_market_for_rotation(markets, None) is None

    def test_current_not_in_list_picks_first_non_expired(self):
        """If the current market was already removed (e.g. closed and
        dropped from the API), pick the next available."""
        now = datetime.now(timezone.utc)
        markets = [
            {"conditionId": "0x_b", "endDate": (now + timedelta(minutes=2)).isoformat()},
        ]
        result = select_next_market_for_rotation(markets, "0x_missing")
        assert result["conditionId"] == "0x_b"

    def test_invalid_enddate_treated_as_not_expired(self):
        """Markets without endDate are kept (conservative)."""
        markets = [
            {"conditionId": "0x_a", "endDate": "not-a-date"},
        ]
        result = select_next_market_for_rotation(markets, None)
        assert result["conditionId"] == "0x_a"


# ──────────────────────────────────────────────────────────────────
# MarketService._matches_live_crypto_window integration
# ──────────────────────────────────────────────────────────────────


class TestMarketServiceLiveCrypto:
    """Verifies MarketService integration with the live crypto matcher."""

    def _make_service(self) -> MarketService:
        mock_port = MagicMock()
        mock_repo = MagicMock()
        mock_redis = MagicMock()
        return MarketService(
            market_data_port=mock_port,
            repository=mock_repo,
            redis=mock_redis,
        )

    def test_live_btc_up_or_down_accepted_as_m5(self):
        svc = self._make_service()
        raw = {
            "title": "Bitcoin Up or Down on June 14, 3:35PM ET",
            "slug": "bitcoin-up-or-down-on-june-14-335pm-et",
        }
        # Bypass asset filter — _matches_window only checks window
        assert svc._matches_live_crypto_window(raw, Window.M5) is True

    def test_live_eth_up_or_down_accepted_as_m5(self):
        svc = self._make_service()
        raw = {
            "title": "Ethereum Up or Down on June 14, 3:35PM ET",
            "slug": "ethereum-up-or-down-on-june-14-335pm-et",
        }
        assert svc._matches_live_crypto_window(raw, Window.M5) is True

    def test_m15_requires_explicit_marker(self):
        svc = self._make_service()
        raw_no_marker = {
            "title": "Bitcoin Up or Down on June 14",
            "slug": "bitcoin-up-or-down-june-14",
        }
        raw_with_marker = {
            "title": "Bitcoin Up or Down on June 14",
            "slug": "bitcoin-up-or-down-15m-june-14",
        }
        assert svc._matches_live_crypto_window(raw_no_marker, Window.M15) is False
        assert svc._matches_live_crypto_window(raw_with_marker, Window.M15) is True

    def test_longevous_market_rejected(self):
        svc = self._make_service()
        raw = {
            "title": "Will bitcoin hit $1m before GTA VI?",
            "slug": "will-bitcoin-hit-1m-before-gta-vi-872-424",
        }
        assert svc._matches_live_crypto_window(raw, Window.M5) is False
        assert svc._matches_live_crypto_window(raw, Window.M15) is False

    def test_unrelated_market_rejected(self):
        svc = self._make_service()
        raw = {
            "title": "Will the Fed cut rates?",
            "slug": "fed-cuts-rates",
        }
        assert svc._matches_live_crypto_window(raw, Window.M5) is False

    def test_matches_window_falls_through_to_live_crypto(self):
        """When dates are missing, _matches_window should still accept
        live crypto markets via the new helper."""
        svc = self._make_service()
        raw = {
            "title": "Bitcoin Up or Down on June 14, 3:35PM ET",
            "slug": "bitcoin-up-or-down-on-june-14-335pm-et",
            # No start_date_iso / end_date_iso
        }
        assert svc._matches_window(raw, Window.M5) is True


# ──────────────────────────────────────────────────────────────────
# Pattern sanity checks
# ──────────────────────────────────────────────────────────────────


class TestPatterns:
    """Smoke tests for the compiled regexes."""

    def test_up_down_pattern_matches_canonical_form(self):
        text = "Bitcoin Up or Down on June 14, 3:35PM ET"
        assert LIVE_UP_DOWN_CRYPTO_PATTERN.search(text) is not None

    def test_up_down_pattern_is_case_insensitive(self):
        text = "ETHEREUM up OR down ON june 14"
        assert LIVE_UP_DOWN_CRYPTO_PATTERN.search(text) is not None

    def test_up_down_pattern_rejects_non_crypto(self):
        text = "S&P 500 Up or Down on June 14"
        assert LIVE_UP_DOWN_CRYPTO_PATTERN.search(text) is None

    def test_price_et_pattern_matches(self):
        text = "Bitcoin Price - June 14 5:00PM ET"
        assert LIVE_PRICE_ET_PATTERN.search(text) is not None

    def test_price_et_pattern_rejects_without_et(self):
        text = "Bitcoin Price - June 14 5:00PM"
        assert LIVE_PRICE_ET_PATTERN.search(text) is None

    def test_market_service_helper_matches_record_helper(self):
        """The helper in market_service.py and record_live_data.py should
        agree on the canonical 'Bitcoin Up or Down' form."""
        raw = {
            "title": "Bitcoin Up or Down on June 14, 3:35PM ET",
            "slug": "btc-up-or-down",
        }
        assert _ms_is_live_crypto(raw) is True
        assert _is_live_crypto_market(raw)[0] is True
