# tests/unit/test_strategy.py

from datetime import datetime, timedelta

import pytest

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.signal_type import SignalType
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal
from src.risk.context import RiskContext
from src.risk.rules.drawdown import DrawdownRule
from src.risk.rules.hedge import HedgeRule
from src.risk.rules.max_exposure import MaxExposureRule
from src.risk.rules.max_positions import MaxPositionsRule
from src.risk.rules.min_balance import MinBalanceRule
from src.strategies.base import StrategyState
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.filters.liquidity_filter import LiquidityFilter
from src.strategies.filters.spread_filter import SpreadFilter
from src.strategies.filters.tick_confirmation import TickConfirmationFilter
from src.strategies.filters.time_filter import TimeFilter

# ── BAT Helpers ──────────────────────────────────────────────────────────────


def make_tick(
    yes_price: float, spread: float = 0.01, volume: float = 5000.0, hour: int = 12
) -> MarketTick:
    """Create a tick at the specified hour to avoid blocked-window flakiness."""
    ts = datetime.utcnow().replace(hour=hour, minute=0, second=0, microsecond=0)
    return MarketTick(
        market_id="test_market",
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=yes_price - spread / 2,
        best_ask=yes_price + spread / 2,
        spread=spread,
        volume_24h=volume,
        timestamp=ts,
    )


def make_market(minutes_to_expiry: float = 60.0) -> Market:
    return Market(
        id="test_market",
        asset=Asset.BTC,
        window=Window.M5,
        question="Test market",
        status=MarketStatus.ACTIVE,
        yes_token_id="yes_token",
        no_token_id="no_token",
        yes_price=0.76,
        no_price=0.24,
        volume_24h=5000.0,
        expiry=datetime.utcnow() + timedelta(minutes=minutes_to_expiry),
    )


# ── Risk Helpers ─────────────────────────────────────────────────────────────


def make_signal(signal_type: SignalType = SignalType.BUY_YES) -> Signal:
    return Signal(
        type=signal_type,
        market_id="test_market",
        confidence=0.8,
        source_strategy="BuyAboveThreshold",
        reason="test",
        timestamp=datetime.utcnow(),
    )


def make_context(**kwargs) -> RiskContext:
    defaults = {
        "current_balance":      1000.0,
        "initial_day_balance":  1000.0,
        "open_positions_count": 0,
        "market_exposure_usdc": 0.0,
        "total_exposure_usdc":  0.0,
        "requested_amount":     10.0,
        "market_id":            "test_market",
        "trading_mode":         "paper",
    }
    defaults.update(kwargs)
    return RiskContext(**defaults)


# ── Filter Helpers ───────────────────────────────────────────────────────────


def make_tick_for_filter(
    yes_price: float = 0.80,
    spread:    float = 0.01,
    volume:    float = 5000.0,
    hour:      int   = 12,
) -> MarketTick:
    ts = datetime.utcnow().replace(hour=hour)
    return MarketTick(
        market_id="test",
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=yes_price - spread / 2,
        best_ask=yes_price + spread / 2,
        spread=spread,
        volume_24h=volume,
        timestamp=ts,
    )


def make_state(consecutive_ticks: int = 0) -> StrategyState:
    state = StrategyState(market_id="test", strategy_name="test")
    state.consecutive_ticks = consecutive_ticks
    return state


# ── BAT Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def strategy():
    config = BuyAboveThresholdConfig(
        threshold=0.75,
        required_ticks=3,
        stop_loss_pct=0.15,
        target_price=0.90,
    )
    return BuyAboveThresholdStrategy(config=config)


@pytest.fixture
def market():
    return make_market()


# ── BAT Tests ────────────────────────────────────────────────────────────────


class TestBuyAboveThreshold:

    @pytest.mark.asyncio
    async def test_hold_when_price_below_threshold(self, strategy, market):
        """No genera señal cuando el precio está bajo el threshold."""
        tick   = make_tick(yes_price=0.70)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)
        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.HOLD
        assert "threshold" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_without_enough_ticks(self, strategy, market):
        """No genera señal sin suficientes ticks de confirmación."""
        tick = make_tick(yes_price=0.80)
        await strategy.on_cycle_start(market)

        # Solo 2 ticks — necesita 3
        for _ in range(2):
            await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.HOLD
        assert "consecutive_ticks" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_yes_signal_after_enough_ticks(self, strategy, market):
        """Genera BUY_YES después de N ticks consecutivos sobre threshold."""
        tick = make_tick(yes_price=0.80, spread=0.01, volume=5000.0)
        await strategy.on_cycle_start(market)

        for _ in range(3):  # required_ticks = 3
            await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.BUY_YES
        assert signal.confidence > 0
        assert signal.source_strategy == "BuyAboveThreshold"

    @pytest.mark.asyncio
    async def test_reset_ticks_when_price_drops(self, strategy, market):
        """Resetea ticks consecutivos cuando el precio cae del threshold."""
        tick_above = make_tick(yes_price=0.80)
        tick_below = make_tick(yes_price=0.70)

        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick_above)
        await strategy.on_tick(market, tick_above)

        state = strategy._states["test_market"]
        assert state.consecutive_ticks == 2

        await strategy.on_tick(market, tick_below)
        assert state.consecutive_ticks == 0

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_exit(self, strategy, market):
        """Genera EXIT cuando la pérdida supera el stop loss."""
        make_tick(yes_price=0.80)
        await strategy.on_cycle_start(market)

        state = strategy._states["test_market"]
        state.record_entry(price=0.80)

        # Precio cae 20% → supera stop loss del 15%
        low_tick = make_tick(yes_price=0.63)
        await strategy.on_tick(market, low_tick)
        signal   = await strategy.should_exit(market, low_tick)

        assert signal.type == SignalType.EXIT
        assert "stop_loss" in signal.reason

    @pytest.mark.asyncio
    async def test_target_reached_triggers_exit(self, strategy, market):
        """Genera EXIT cuando el precio alcanza el target."""
        make_tick(yes_price=0.80)
        await strategy.on_cycle_start(market)

        state = strategy._states["test_market"]
        state.record_entry(price=0.80)

        high_tick = make_tick(yes_price=0.92)  # > target 0.90
        await strategy.on_tick(market, high_tick)
        signal    = await strategy.should_exit(market, high_tick)

        assert signal.type == SignalType.EXIT
        assert "target_reached" in signal.reason

    @pytest.mark.asyncio
    async def test_no_entry_when_market_expiring(self, strategy):
        """No entra si el mercado expira en < 5 minutos."""
        market = make_market(minutes_to_expiry=3.0)
        tick   = make_tick(yes_price=0.85)

        await strategy.on_cycle_start(market)
        for _ in range(5):
            await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.HOLD
        assert "expiring_soon" in signal.reason

    @pytest.mark.asyncio
    async def test_confidence_increases_with_price(self, strategy, market):
        """La confidence es mayor cuanto más lejos está el precio del threshold."""
        tick_close = make_tick(yes_price=0.76)  # Cerca del threshold (0.75)
        tick_far   = make_tick(yes_price=0.90)  # Lejos del threshold

        await strategy.on_cycle_start(market)

        # Genera señal para precio cercano
        for _ in range(3):
            await strategy.on_tick(market, tick_close)
        signal_close = await strategy.should_enter(market, tick_close)

        # Resetea y genera señal para precio lejano
        state = strategy._states["test_market"]
        state.reset_tick_buffer()
        state.consecutive_ticks = 0

        for _ in range(3):
            await strategy.on_tick(market, tick_far)
        signal_far = await strategy.should_enter(market, tick_far)

        if signal_close.type == SignalType.BUY_YES and signal_far.type == SignalType.BUY_YES:
            assert signal_far.confidence > signal_close.confidence


# ── Risk Tests ───────────────────────────────────────────────────────────────


class TestMinBalanceRule:

    def test_denies_when_balance_would_drop_below_minimum(self):
        rule    = MinBalanceRule(min_balance_usdc=50.0)
        context = make_context(current_balance=55.0, requested_amount=10.0)
        # 55 - 10 = 45 < 50 → DENY
        result  = rule.evaluate(make_signal(), context)
        assert not result.allowed
        assert "MinBalanceRule" in result.rule_triggered

    def test_allows_when_balance_sufficient(self):
        rule    = MinBalanceRule(min_balance_usdc=50.0)
        context = make_context(current_balance=200.0, requested_amount=10.0)
        result  = rule.evaluate(make_signal(), context)
        assert result.allowed


class TestDrawdownRule:

    def test_denies_when_drawdown_exceeds_limit(self):
        rule    = DrawdownRule(max_daily_drawdown_pct=0.10)
        context = make_context(
            current_balance=880.0,
            initial_day_balance=1000.0,
        )
        # Drawdown = (1000-880)/1000 = 12% > 10%
        result = rule.evaluate(make_signal(), context)
        assert not result.allowed
        assert "DrawdownRule" in result.rule_triggered

    def test_allows_when_drawdown_within_limit(self):
        rule    = DrawdownRule(max_daily_drawdown_pct=0.10)
        context = make_context(
            current_balance=950.0,
            initial_day_balance=1000.0,
        )
        # Drawdown = 5% < 10%
        result = rule.evaluate(make_signal(), context)
        assert result.allowed


class TestMaxExposureRule:

    def test_adjusts_amount_when_partially_available(self):
        rule    = MaxExposureRule(max_exposure_pct=0.30)
        context = make_context(
            current_balance=1000.0,
            market_exposure_usdc=250.0,   # Ya 250 de 300 máximos
            requested_amount=100.0,
        )
        # Disponible: 300 - 250 = 50 USDC → ajusta a 50
        result = rule.evaluate(make_signal(), context)
        assert result.allowed
        assert result.suggested_amount == 50.0

    def test_denies_when_no_room_available(self):
        rule    = MaxExposureRule(max_exposure_pct=0.30)
        context = make_context(
            current_balance=1000.0,
            market_exposure_usdc=300.0,   # Ya en el límite
            requested_amount=10.0,
        )
        result = rule.evaluate(make_signal(), context)
        assert not result.allowed


class TestMaxPositionsRule:

    def test_always_allows_exit_signals(self):
        rule    = MaxPositionsRule(max_open_positions=2)
        context = make_context(open_positions_count=5)  # Límite superado
        # Pero es una señal EXIT → siempre se permite
        result  = rule.evaluate(make_signal(SignalType.EXIT), context)
        assert result.allowed

    def test_denies_new_entry_at_max(self):
        rule    = MaxPositionsRule(max_open_positions=3)
        context = make_context(open_positions_count=3)
        result  = rule.evaluate(make_signal(SignalType.BUY_YES), context)
        assert not result.allowed


class TestHedgeRule:

    def test_only_applies_to_buy_no(self):
        rule    = HedgeRule(max_net_exposure_pct=0.50)
        context = make_context(total_exposure_usdc=800.0, current_balance=1000.0)
        # Para BUY_YES → no aplica, permite siempre
        result  = rule.evaluate(make_signal(SignalType.BUY_YES), context)
        assert result.allowed

    def test_denies_hedge_when_overexposed(self):
        rule    = HedgeRule(max_net_exposure_pct=0.50)
        context = make_context(
            total_exposure_usdc=600.0,   # 60% > 50%
            current_balance=1000.0,
        )
        result = rule.evaluate(make_signal(SignalType.BUY_NO), context)
        assert not result.allowed


# ── Filter Tests ─────────────────────────────────────────────────────────────


class TestFilters:

    def test_spread_filter_passes(self):
        f      = SpreadFilter(max_spread=0.03)
        result = f.apply(make_tick_for_filter(spread=0.01), make_state())
        assert result.passed

    def test_spread_filter_fails(self):
        f      = SpreadFilter(max_spread=0.03)
        result = f.apply(make_tick_for_filter(spread=0.05), make_state())
        assert not result.passed
        assert "SpreadFilter" in result.filter_name

    def test_liquidity_filter_passes(self):
        f      = LiquidityFilter(min_volume_pusd=1000.0)
        result = f.apply(make_tick_for_filter(volume=5000.0), make_state())
        assert result.passed

    def test_liquidity_filter_fails(self):
        f      = LiquidityFilter(min_volume_pusd=1000.0)
        result = f.apply(make_tick_for_filter(volume=500.0), make_state())
        assert not result.passed

    def test_time_filter_blocks_restricted_hours(self):
        f      = TimeFilter(blocked_hours=[(0, 6)])
        result = f.apply(make_tick_for_filter(hour=3), make_state())
        assert not result.passed

    def test_time_filter_allows_normal_hours(self):
        f      = TimeFilter(blocked_hours=[(0, 6)])
        result = f.apply(make_tick_for_filter(hour=14), make_state())
        assert result.passed

    def test_tick_confirmation_passes_enough_ticks(self):
        f      = TickConfirmationFilter(required_ticks=3)
        result = f.apply(make_tick_for_filter(), make_state(consecutive_ticks=3))
        assert result.passed

    def test_tick_confirmation_fails_insufficient_ticks(self):
        f      = TickConfirmationFilter(required_ticks=3)
        result = f.apply(make_tick_for_filter(), make_state(consecutive_ticks=2))
        assert not result.passed
