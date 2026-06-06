# tests/property/test_strategy_invariants.py
"""Property-based tests for strategy invariants (P3.2).

Invariants under test:
  - BUY_YES never generated when price < threshold  (∀ valid params)
  - Confidence always in [0.0, 1.0]                (∀ ticks, configs)
  - Exit only triggered when in_position=True
  - Stop loss exit only when loss > stop_loss_pct
  - HOLD when market expired / about to expire
  - MeanReversion: HOLD when z_score >= entry_zscore
"""

from datetime import datetime, timedelta

import pytest
import structlog
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.signal_type import SignalType
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.strategies.base import StrategyState
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.mean_reversion.config import MeanReversionConfig
from src.strategies.mean_reversion.strategy import MeanReversionStrategy, _compute_zscore

# Suprimir ruido de structlog durante tests de property
structlog.configure(processors=[structlog.processors.KeyValueRenderer()])

# ── Strategies para Hypothesis ────────────────────────────────────────────

# Precio YES: 0.01 a 0.99
yes_price_st = st.floats(min_value=0.01, max_value=0.99, allow_infinity=False, allow_nan=False)

# Threshold: 0.30 a 0.80 (deja margen para target_price y stop_drop_floor)
threshold_st = st.floats(min_value=0.30, max_value=0.80, allow_infinity=False, allow_nan=False)

# Minutes to expiry: 0 to 120
expiry_minutes_st = st.floats(min_value=0.0, max_value=120.0, allow_infinity=False, allow_nan=False)

# Confidence: 0.0 a 1.0
confidence_st = st.floats(min_value=0.0, max_value=1.0, allow_infinity=False, allow_nan=False)

# Entry zscore: -4.0 a -1.0
entry_zscore_st = st.floats(min_value=-4.0, max_value=-1.0, allow_infinity=False, allow_nan=False)

# Entry price: 0.01 a 0.99
entry_price_st = st.floats(min_value=0.01, max_value=0.99, allow_infinity=False, allow_nan=False)

# Stop loss pct: 0.05 a 0.30
stop_loss_st = st.floats(min_value=0.05, max_value=0.30, allow_infinity=False, allow_nan=False)


# ── Helpers ────────────────────────────────────────────────────────────────

def make_market(
    market_id: str = "test_market",
    asset: Asset = Asset.BTC,
    window: Window = Window.M5,
    status: MarketStatus = MarketStatus.ACTIVE,
    expiry: datetime | None = None,
    yes_price: float = 0.60,
) -> Market:
    """Crea un Market sintético para property tests (usa los campos reales del entity)."""
    return Market(
        id=market_id,
        question=f"Will {asset.value} price be above X at time T?",
        asset=asset,
        window=window,
        status=status,
        yes_token_id="yes_token_001",
        no_token_id="no_token_001",
        yes_price=yes_price,
        no_price=round(1.0 - yes_price, 4),
        volume_24h=5000.0,
        expiry=expiry or (datetime.utcnow() + timedelta(hours=4)),
        discovered_at=datetime.utcnow() - timedelta(days=1),
        updated_at=datetime.utcnow(),
    )


def make_tick(yes_price: float, spread: float = 0.005, volume: float = 5000.0) -> MarketTick:
    """Crea un MarketTick sintético para property tests."""
    return MarketTick(
        market_id="test_market",
        yes_price=yes_price,
        no_price=round(1.0 - yes_price, 4),
        spread=spread,
        volume_24h=volume,
        best_bid=round(yes_price - spread / 2, 4),
        best_ask=round(yes_price + spread / 2, 4),
        timestamp=datetime.utcnow(),
    )


def make_bat_config(threshold: float) -> BuyAboveThresholdConfig:
    """
    Crea una config BAT válida derivando valores dependientes del threshold.
    Garantiza que stop_drop_floor < threshold < target_price para pasar validate().
    También desactiva blocked_hours para evitar tests flaky en madrugada UTC.
    """
    return BuyAboveThresholdConfig(
        threshold=threshold,
        required_ticks=1,
        max_spread=0.10,            # Permisivo para no interferir
        min_volume_pusd=100.0,      # Permisivo
        blocked_hours=[],           # ⚠️ Sin restricción horaria (evita flaky tests)
        stop_loss_pct=0.15,
        stop_drop_floor=round(threshold * 0.6, 2),  # Siempre < threshold
        timeout_minutes=30.0,
        target_price=round(min(threshold + 0.15, 0.99), 2),  # Siempre > threshold
        hedge_drop_pct=0.20,
        hedge_enabled=False,        # Desactivado para tests limpios
        position_size_pusd=10.0,
    )


def make_state(market_id: str = "test_market", strategy_name: str = "Test"):
    """Crea un StrategyState mutable."""
    return StrategyState(market_id=market_id, strategy_name=strategy_name)


# ═════════════════════════════════════════════════════════════════════════════
# BUY ABOVE THRESHOLD — INVARIANTS
# ═════════════════════════════════════════════════════════════════════════════

class TestBATPriceBelowThresholdNeverBuys:
    """
    Invariante: BUY_YES is NEVER generated when price < threshold.
    Para cualquier combinación válida de (price, threshold) con price < threshold,
    should_enter SIEMPRE debe devolver HOLD (no BUY_YES).
    """

    @given(
        yes_price=yes_price_st,
        threshold=threshold_st,
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_price_below_threshold_returns_hold(self, yes_price, threshold):
        """∀ price < threshold → should_enter returns HOLD."""
        assume(yes_price < threshold)  # Solo interesa el caso price < threshold

        config = make_bat_config(threshold)
        strategy = BuyAboveThresholdStrategy(config=config)
        market = make_market()
        tick = make_tick(yes_price=yes_price, spread=0.005, volume=5000.0)

        state = strategy._get_or_create_state(market.id)
        for _ in range(config.required_ticks):
            state.consecutive_ticks += 1
        state.add_tick(tick)

        signal = await strategy.should_enter(market, tick)

        assert signal.type != SignalType.BUY_YES, (
            f"price={yes_price:.4f} < threshold={threshold:.4f} "
            f"but got {signal.type.value} — should be HOLD"
        )


class TestBATConfidenceInRange:
    """
    Invariante: confidence always in [0.0, 1.0].
    Para cualquier tick y configuración válida, el confidence de una señal
    de entrada debe estar en el rango esperado.
    """

    @given(
        yes_price=st.floats(min_value=0.01, max_value=0.99, allow_infinity=False, allow_nan=False),
        threshold=st.floats(min_value=0.01, max_value=0.80, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_confidence_in_range(self, yes_price, threshold):
        """∀ valid params: Signal.confidence ∈ [0.0, 1.0]."""
        # Skip invalid configs
        assume(threshold > 0.05)

        config = make_bat_config(threshold)
        strategy = BuyAboveThresholdStrategy(config=config)
        market = make_market()
        tick = make_tick(yes_price=yes_price, spread=0.003, volume=5000.0)

        state = strategy._get_or_create_state(market.id)
        for _ in range(config.required_ticks):
            state.consecutive_ticks += 1
        state.add_tick(tick)

        signal = await strategy.should_enter(market, tick)

        assert 0.0 <= signal.confidence <= 1.0, (
            f"confidence={signal.confidence:.4f} not in [0.0, 1.0] "
            f"(price={yes_price:.4f}, threshold={threshold:.4f})"
        )


class TestBATExitOnlyWhenInPosition:
    """
    Invariante: Exit only triggered when in_position=True.
    should_exit NUNCA debe devolver EXIT si no hay posición abierta.
    """

    @given(
        yes_price=yes_price_st,
        threshold=threshold_st,
        in_position=st.booleans(),
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_exit_only_in_position(self, yes_price, threshold, in_position):
        """∀ tick, config: if not in_position → should_exit never EXIT."""
        config = make_bat_config(threshold)
        strategy = BuyAboveThresholdStrategy(config=config)
        market = make_market()
        tick = make_tick(yes_price=yes_price)

        state = strategy._get_or_create_state(market.id)
        if in_position:
            state.record_entry(price=yes_price)

        signal = await strategy.should_exit(market, tick)

        if not in_position:
            assert signal.type != SignalType.EXIT, (
                f"Expected HOLD when not in position, got {signal.type.value} "
                f"(price={yes_price:.4f}, in_position={in_position})"
            )


class TestBATHoldWhenExpired:
    """
    Invariante: HOLD when market expired or about to expire (< 5 min).
    """

    @given(
        yes_price=yes_price_st,
        threshold=threshold_st,
        minutes_to_expiry=st.floats(min_value=0.0, max_value=10.0, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_expiring_market_hold(self, yes_price, threshold, minutes_to_expiry):
        """∀ market with < 5min to expiry: should_enter returns HOLD."""
        config = make_bat_config(threshold)
        strategy = BuyAboveThresholdStrategy(config=config)
        expiry = datetime.utcnow() + timedelta(minutes=minutes_to_expiry)
        market = make_market(expiry=expiry)
        tick = make_tick(yes_price=yes_price, spread=0.005, volume=5000.0)

        state = strategy._get_or_create_state(market.id)
        for _ in range(config.required_ticks):
            state.consecutive_ticks += 1

        signal = await strategy.should_enter(market, tick)

        if minutes_to_expiry < 5.0:
            assert signal.type != SignalType.BUY_YES, (
                f"Expected HOLD for expiring market ({minutes_to_expiry:.1f}min), "
                f"got {signal.type.value}"
            )


class TestBATStopLossInvariant:
    """
    Invariante: Stop loss exit only when loss > stop_loss_pct.
    """

    @given(
        entry_price=entry_price_st,
        current_price=entry_price_st,
        stop_loss_pct=stop_loss_st,
        threshold=threshold_st,
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_stop_loss_only_when_loss_exceeds(self, entry_price, current_price, stop_loss_pct, threshold):
        """∀ prices: stop loss EXIT only if (current - entry)/entry <= -stop_loss_pct."""
        config = make_bat_config(threshold)
        # Override stop_loss_pct for this test
        config.stop_loss_pct = stop_loss_pct
        # Set stop_drop_floor very low to avoid interference
        config.stop_drop_floor = 0.01

        strategy = BuyAboveThresholdStrategy(config=config)
        market = make_market()
        tick = make_tick(yes_price=current_price)

        state = strategy._get_or_create_state(market.id)
        state.record_entry(price=entry_price)

        signal = await strategy.should_exit(market, tick)

        loss_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
        is_stop_loss = signal.type == SignalType.EXIT and "stop_loss" in signal.reason.lower()

        if is_stop_loss:
            assert loss_pct <= -stop_loss_pct, (
                f"Stop loss triggered but loss={loss_pct:.4%} > -{stop_loss_pct:.2%} "
                f"(entry={entry_price:.4f}, current={current_price:.4f})"
            )


# ═════════════════════════════════════════════════════════════════════════════
# MEAN REVERSION — INVARIANTS
# ═════════════════════════════════════════════════════════════════════════════

def make_mr_config(
    ma_window: int = 5,
    entry_zscore: float = -2.0,
    exit_zscore: float = 0.0,
    stop_loss_pct: float = 0.10,
) -> MeanReversionConfig:
    """Crea una config MeanReversion válida sin restricción horaria."""
    return MeanReversionConfig(
        ma_window=ma_window,
        entry_zscore=entry_zscore,
        exit_zscore=exit_zscore,
        stop_loss_pct=stop_loss_pct,
        timeout_minutes=45.0,
        max_spread=0.10,
        min_volume_pusd=100.0,
        blocked_hours=[],       # ⚠️ Sin restricción horaria
        position_size_pusd=10.0,
    )


class TestMRHoldWhenZScoreAboveEntry:
    """
    Invariante: MeanReversion should_enter returns HOLD when z_score >= entry_zscore.
    """

    @given(
        yes_price=yes_price_st,
        entry_zscore=entry_zscore_st,
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_hold_when_not_oversold(self, yes_price, entry_zscore):
        """∀ price, entry_zscore: if z_score >= entry_zscore → HOLD."""
        config = make_mr_config(entry_zscore=entry_zscore)
        strategy = MeanReversionStrategy(config=config)
        market = make_market()
        tick = make_tick(yes_price=yes_price, spread=0.003, volume=5000.0)

        state = strategy._get_or_create_state(market.id)
        # Fill buffer with same price → SMA = yes_price, z_score ≈ 0
        for _ in range(config.ma_window):
            state.add_tick(tick)

        signal = await strategy.should_enter(market, tick)
        z_score = _compute_zscore(tick, state.tick_buffer, config.ma_window)

        if z_score >= entry_zscore:
            assert signal.type != SignalType.BUY_YES, (
                f"Expected HOLD when z_score={z_score:.3f} >= "
                f"entry_zscore={entry_zscore:.3f}, got {signal.type.value}"
            )


class TestMRConfidenceInRange:
    """
    Invariante: MeanReversion confidence ∈ [0.0, 1.0].
    """

    @given(
        yes_price=yes_price_st,
        entry_zscore=entry_zscore_st,
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_confidence_in_range(self, yes_price, entry_zscore):
        """∀ valid params: MR Signal.confidence ∈ [0.0, 1.0]."""
        config = make_mr_config(entry_zscore=entry_zscore)
        strategy = MeanReversionStrategy(config=config)
        market = make_market()
        tick = make_tick(yes_price=yes_price, spread=0.003, volume=5000.0)

        state = strategy._get_or_create_state(market.id)
        for _ in range(config.ma_window):
            state.add_tick(tick)

        signal = await strategy.should_enter(market, tick)

        assert 0.0 <= signal.confidence <= 1.0, (
            f"MR confidence={signal.confidence:.4f} not in [0.0, 1.0]"
        )


class TestMRExitOnlyInPosition:
    """
    Invariante: MeanReversion should_exit solo genera EXIT si in_position=True.
    """

    @given(
        yes_price=yes_price_st,
        in_position=st.booleans(),
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_exit_only_in_position(self, yes_price, in_position):
        """∀ price: MR should_exit only EXIT when in_position."""
        config = make_mr_config()
        strategy = MeanReversionStrategy(config=config)
        market = make_market()
        tick = make_tick(yes_price=yes_price)

        state = strategy._get_or_create_state(market.id)
        if in_position:
            state.record_entry(price=yes_price)
        for _ in range(config.ma_window):
            state.add_tick(tick)

        signal = await strategy.should_exit(market, tick)

        if not in_position:
            assert signal.type != SignalType.EXIT, (
                f"MR: Expected HOLD when not in position, got {signal.type.value}"
            )


class TestMRStopLossInvariant:
    """
    Invariante: MR stop loss solo cuando loss > stop_loss_pct.
    """

    @given(
        entry_price=entry_price_st,
        current_price=entry_price_st,
        stop_loss_pct=stop_loss_st,
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_stop_loss_only_when_loss_exceeds(self, entry_price, current_price, stop_loss_pct):
        """∀ prices: MR stop loss EXIT only if loss exceeds threshold."""
        config = make_mr_config(stop_loss_pct=stop_loss_pct)
        strategy = MeanReversionStrategy(config=config)
        market = make_market()
        tick = make_tick(yes_price=current_price)

        state = strategy._get_or_create_state(market.id)
        state.record_entry(price=entry_price)
        for _ in range(config.ma_window):
            state.add_tick(tick)

        signal = await strategy.should_exit(market, tick)

        loss_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
        is_stop_loss = signal.type == SignalType.EXIT and "stop_loss" in signal.reason.lower()

        if is_stop_loss:
            assert loss_pct <= -stop_loss_pct, (
                f"MR stop loss triggered but loss={loss_pct:.4%} > -{stop_loss_pct:.2%}"
            )
