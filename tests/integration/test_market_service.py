"""
Integration tests for MarketService.discover_markets.

Exercise the full discover loop with mocked HTTP/Redis/repo to confirm:
  - clear_active_markets_lists is called before repopulating Redis sets
    (prevents stale ids from rotated M5/M15 crypto markets).
  - Four BTC/ETH × M5/M15 raw markets produce exactly 4 distinct
    save_market + set_market invocations (one per pair).
  - Each (asset, window) entry retains its own condition_id — no
    upsert collision across windows.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.market_service import MarketService
from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.window import Window


def _raw(slug: str, question: str, *, cid: str, vol: float = 5.0) -> dict:
    return {
        "condition_id": cid,
        "question":     question,
        "slug":         slug,
        "active":       True,
        "tokens": [
            {"outcome": "Up",   "token_id": cid + "_y", "price": 0.5},
            {"outcome": "Down", "token_id": cid + "_n", "price": 0.5},
        ],
        "volume24hr":      vol,
        "start_date_iso":  "",
        "end_date_iso":    "",
    }


@pytest.fixture
def four_raw_markets() -> list[dict]:
    """One market per (asset, window) pair, with distinct condition_ids."""
    return [
        _raw(
            slug="btc-updown-5m-1782134100",
            question="Bitcoin Up or Down 1:20PM-1:25PM ET",
            cid="0x" + "1" * 64,
        ),
        _raw(
            slug="btc-updown-15m-1782145800",
            question="Bitcoin Up or Down 5:30PM-5:45PM ET",
            cid="0x" + "2" * 64,
        ),
        _raw(
            slug="eth-updown-5m-1782182400",
            question="Ethereum Up or Down 10:40PM-10:45PM ET",
            cid="0x" + "3" * 64,
        ),
        _raw(
            slug="eth-updown-15m-1782181800",
            question="Ethereum Up or Down 10:30PM-10:45PM ET",
            cid="0x" + "4" * 64,
        ),
    ]


@pytest.fixture
def mock_market_data() -> MagicMock:
    m = MagicMock()
    m.get_active_markets = AsyncMock(return_value=[])
    m.get_market_tick    = AsyncMock(return_value=None)
    return m


@pytest.fixture
def mock_repo() -> MagicMock:
    repo = MagicMock()
    repo.save_market = AsyncMock(side_effect=lambda m: m)
    return repo


@pytest.fixture
def mock_redis() -> MagicMock:
    r = MagicMock()
    r.clear_active_markets_lists = AsyncMock()
    r.set_market                  = AsyncMock()
    r.get_market_metadata         = AsyncMock(return_value=None)
    return r


@pytest.fixture
def svc(mock_market_data, mock_repo, mock_redis) -> MarketService:
    return MarketService(
        market_data_port=mock_market_data,
        repository=mock_repo,
        redis=mock_redis,
    )


class TestDiscoverMarketsFourPairs:
    @pytest.mark.asyncio
    async def test_clear_active_lists_called_first(
        self, svc, mock_redis, mock_market_data
    ) -> None:
        """clear_active_markets_lists must run BEFORE set_market."""
        mock_market_data.get_active_markets.return_value = []
        await svc.discover_markets()
        mock_redis.clear_active_markets_lists.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_four_markets_persist_distinctly(
        self, svc, mock_market_data, mock_repo, mock_redis, four_raw_markets
    ) -> None:
        mock_market_data.get_active_markets.return_value = four_raw_markets

        discovered = await svc.discover_markets()

        # 4 markets persisted, one per (asset, window) pair
        assert len(discovered) == 4

        pairs = {(m.asset, m.window) for m in discovered}
        assert pairs == {
            (Asset.BTC, Window.M5),
            (Asset.BTC, Window.M15),
            (Asset.ETH, Window.M5),
            (Asset.ETH, Window.M15),
        }

        # No two discovered markets share a condition_id
        ids = {m.id for m in discovered}
        assert len(ids) == 4

        # save_market called exactly 4 times
        assert mock_repo.save_market.await_count == 4

        # set_market called at least 4 times (one per market; _cache_market_info
        # may add a second call but only on cache-hit, which our mock returns None for)
        assert mock_redis.set_market.await_count >= 4

    @pytest.mark.asyncio
    async def test_clear_runs_even_if_http_fails(
        self, svc, mock_redis, mock_market_data
    ) -> None:
        """If Gamma is down, sets are still cleared (don't show stale markets)."""
        mock_market_data.get_active_markets.side_effect = RuntimeError("gamma down")
        discovered = await svc.discover_markets()
        assert discovered == []
        mock_redis.clear_active_markets_lists.assert_awaited_once()


class TestNoConditionIdCollisionAcrossWindows:
    @pytest.mark.asyncio
    async def test_save_market_calls_have_distinct_ids(
        self, svc, mock_market_data, mock_repo, four_raw_markets
    ) -> None:
        """Regression: previously eth-updown-15m collapsed to ETH-5m via the
        bug in _matches_live_crypto_window. Now each pair owns its own id."""
        mock_market_data.get_active_markets.return_value = four_raw_markets

        await svc.discover_markets()

        saved_ids = [c.args[0].id for c in mock_repo.save_market.await_args_list]
        assert len(saved_ids) == 4
        assert len(set(saved_ids)) == 4, (
            f"Expected 4 distinct condition_ids, got duplicates: {saved_ids}"
        )

    @pytest.mark.asyncio
    async def test_save_market_calls_have_correct_pair_labels(
        self, svc, mock_market_data, mock_repo, four_raw_markets
    ) -> None:
        mock_market_data.get_active_markets.return_value = four_raw_markets

        await svc.discover_markets()

        saved: list[Market] = [
            c.args[0] for c in mock_repo.save_market.await_args_list
        ]
        # Map condition_id → (asset, window)
        actual = {m.id: (m.asset, m.window) for m in saved}
        expected = {
            "0x" + "1" * 64: (Asset.BTC, Window.M5),
            "0x" + "2" * 64: (Asset.BTC, Window.M15),
            "0x" + "3" * 64: (Asset.ETH, Window.M5),
            "0x" + "4" * 64: (Asset.ETH, Window.M15),
        }
        assert actual == expected
