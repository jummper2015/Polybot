# tests/unit/test_multi_timeframe.py

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.strategies.base import StrategyState
from src.strategies.filters.multi_timeframe import (
    FILTER_NAME,
    MultiTimeframeFilter,
)


def make_m5_tick(yes_price: float = 0.80) -> MarketTick:
    return MarketTick(
        market_id="test_m5",
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=yes_price - 0.005,
        best_ask=yes_price + 0.005,
        spread=0.01,
        volume_24h=5000.0,
        timestamp=datetime.utcnow(),
    )


def make_m15_tick(yes_price: float = 0.80) -> MarketTick:
    return MarketTick(
        market_id="test_m15",
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=yes_price - 0.005,
        best_ask=yes_price + 0.005,
        spread=0.01,
        volume_24h=5000.0,
        timestamp=datetime.utcnow(),
    )


def make_market(window: Window = Window.M5) -> Market:
    from datetime import timedelta
    return Market(
        id="test_m5" if window == Window.M5 else "test_m15",
        asset=Asset.BTC,
        window=window,
        question="Test BTC market",
        status=MarketStatus.ACTIVE,
        yes_token_id="yes_token",
        no_token_id="no_token",
        yes_price=0.80,
        no_price=0.20,
        volume_24h=5000.0,
        expiry=datetime.utcnow() + timedelta(minutes=60),
    )


def make_state() -> StrategyState:
    return StrategyState(market_id="test_m5", strategy_name="BuyAboveThreshold")


class TestMultiTimeframeFilter:

    @pytest.mark.asyncio
    async def test_skips_m15_markets(self):
        """No aplica a mercados M15 — siempre OK."""
        mock_provider = AsyncMock()
        f = MultiTimeframeFilter(tick_provider=mock_provider, threshold=0.75)

        market = make_market(window=Window.M15)
        tick = make_m5_tick(yes_price=0.80)
        state = make_state()

        result = await f.apply(tick, state, market)
        assert result.passed
        assert "not M5" in result.reason
        mock_provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirms_when_m15_above_threshold(self):
        """M15 yes_price >= threshold → OK (confirma)."""
        mock_provider = AsyncMock(return_value=make_m15_tick(yes_price=0.80))
        f = MultiTimeframeFilter(tick_provider=mock_provider, threshold=0.75)

        market = make_market(window=Window.M5)
        tick = make_m5_tick(yes_price=0.82)
        state = make_state()

        result = await f.apply(tick, state, market)
        assert result.passed
        assert "M15 confirms" in result.reason

    @pytest.mark.asyncio
    async def test_rejects_when_m15_below_threshold(self):
        """M15 yes_price < threshold → FAIL (no confirma)."""
        mock_provider = AsyncMock(return_value=make_m15_tick(yes_price=0.70))
        f = MultiTimeframeFilter(tick_provider=mock_provider, threshold=0.75)

        market = make_market(window=Window.M5)
        tick = make_m5_tick(yes_price=0.80)
        state = make_state()

        result = await f.apply(tick, state, market)
        assert not result.passed
        assert "does NOT confirm" in result.reason

    @pytest.mark.asyncio
    async def test_allows_when_no_m15_tick(self):
        """Si no hay tick M15 → OK (no bloquear por falta de datos)."""
        mock_provider = AsyncMock(return_value=None)
        f = MultiTimeframeFilter(tick_provider=mock_provider, threshold=0.75)

        market = make_market(window=Window.M5)
        tick = make_m5_tick(yes_price=0.80)
        state = make_state()

        result = await f.apply(tick, state, market)
        assert result.passed
        assert "no M15 tick" in result.reason

    @pytest.mark.asyncio
    async def test_allows_when_provider_raises(self):
        """Si el tick_provider lanza excepción → OK (graceful degradation)."""
        mock_provider = AsyncMock(side_effect=RuntimeError("network error"))
        f = MultiTimeframeFilter(tick_provider=mock_provider, threshold=0.75)

        market = make_market(window=Window.M5)
        tick = make_m5_tick(yes_price=0.80)
        state = make_state()

        result = await f.apply(tick, state, market)
        assert result.passed
        assert "failed" in result.reason

    @pytest.mark.asyncio
    async def test_passes_asset_to_provider(self):
        """Verifica que el tick_provider recibe el asset correcto."""
        mock_provider = AsyncMock(return_value=make_m15_tick(yes_price=0.80))
        f = MultiTimeframeFilter(tick_provider=mock_provider, threshold=0.75)

        market = make_market(window=Window.M5)  # Asset.BTC
        tick = make_m5_tick(yes_price=0.80)
        state = make_state()

        await f.apply(tick, state, market)
        mock_provider.assert_called_once_with(market.asset)
        assert mock_provider.call_args[0][0] == Asset.BTC

    @pytest.mark.asyncio
    async def test_result_has_filter_name(self):
        """FilterResult incluye el nombre del filtro para métricas."""
        mock_provider = AsyncMock(return_value=make_m15_tick(yes_price=0.70))
        f = MultiTimeframeFilter(tick_provider=mock_provider, threshold=0.75)

        market = make_market(window=Window.M5)
        tick = make_m5_tick(yes_price=0.80)
        state = make_state()

        result = await f.apply(tick, state, market)
        assert result.filter_name == FILTER_NAME
