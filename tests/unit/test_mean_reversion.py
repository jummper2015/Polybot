# tests/unit/test_mean_reversion.py

from datetime import datetime, timedelta

import pytest

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.signal_type import SignalType
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.strategies.mean_reversion.config import MeanReversionConfig
from src.strategies.mean_reversion.strategy import (
    MeanReversionStrategy,
    _compute_zscore,
)


def make_tick(
    market_id: str = "test_market",
    yes_price: float = 0.50,
    spread: float = 0.01,
    volume: float = 5000.0,
    hour: int = 12,
) -> MarketTick:
    ts = datetime.utcnow().replace(hour=hour)
    return MarketTick(
        market_id=market_id,
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=yes_price - spread / 2,
        best_ask=yes_price + spread / 2,
        spread=spread,
        volume_24h=volume,
        timestamp=ts,
    )


def make_market(
    market_id: str = "test_market",
    minutes_to_expiry: float = 60.0,
) -> Market:
    return Market(
        id=market_id,
        asset=Asset.BTC,
        window=Window.M5,
        question="Test market",
        status=MarketStatus.ACTIVE,
        yes_token_id="yes_token",
        no_token_id="no_token",
        yes_price=0.50,
        no_price=0.50,
        volume_24h=5000.0,
        expiry=datetime.utcnow() + timedelta(minutes=minutes_to_expiry),
    )


def fill_buffer(strategy: MeanReversionStrategy, market: Market, prices: list[float]):
    """
    Helper: llena el buffer de ticks con los precios dados.
    Llama on_tick para cada precio. Los ticks tienen spread/volume válidos.
    """
    for price in prices:
        tick = make_tick(yes_price=price, market_id=market.id)
        strategy._get_or_create_state(market.id).add_tick(tick)


@pytest.fixture
def strategy():
    config = MeanReversionConfig(
        ma_window=20,
        entry_zscore=-2.0,
        exit_zscore=0.0,
        stop_loss_pct=0.10,
        timeout_minutes=45.0,
    )
    return MeanReversionStrategy(config=config)


@pytest.fixture
def market():
    return make_market()


class TestComputeZScore:
    """Tests para la función auxiliar _compute_zscore."""

    def test_sma_calculation_correct(self):
        """Verifica que SMA y z-score se calculan correctamente."""
        # SMA de [0.50, 0.48, 0.46, 0.44, 0.42] = 0.46
        # std ≈ 0.02828
        # precio actual = 0.40 → z_score = (0.40 - 0.46) / 0.02828 ≈ -2.12
        prices = [0.50, 0.48, 0.46, 0.44, 0.42]
        buffer = [make_tick(yes_price=p) for p in prices]
        current_tick = make_tick(yes_price=0.40)

        z = _compute_zscore(current_tick, buffer, ma_window=5)
        assert z < -2.0, f"Expected z < -2.0, got {z:.3f}"
        assert z > -2.5, f"Expected z > -2.5, got {z:.3f}"

    def test_zscore_zero_when_insufficient_buffer(self):
        """z-score = 0 si no hay suficientes ticks."""
        buffer = [make_tick(yes_price=0.50) for _ in range(3)]
        current_tick = make_tick(yes_price=0.40)

        z = _compute_zscore(current_tick, buffer, ma_window=5)
        assert z == 0.0

    def test_zscore_zero_when_zero_std(self):
        """z-score = 0 si todos los precios son iguales (std ≈ 0)."""
        buffer = [make_tick(yes_price=0.50) for _ in range(5)]
        current_tick = make_tick(yes_price=0.50)

        z = _compute_zscore(current_tick, buffer, ma_window=5)
        assert z == 0.0

    def test_zscore_positive_when_above_sma(self):
        """z-score positivo cuando el precio está sobre la SMA."""
        # SMA de [0.50, 0.50, 0.50, 0.50, 0.50] = 0.50, std = 0
        # → z = 0 (por std cero)
        # Usamos precios variables para tener std > 0
        prices = [0.40, 0.42, 0.44, 0.46, 0.48]
        buffer = [make_tick(yes_price=p) for p in prices]
        current_tick = make_tick(yes_price=0.60)  # muy por encima

        z = _compute_zscore(current_tick, buffer, ma_window=5)
        assert z > 1.0, f"Expected z > 1.0, got {z:.3f}"


class TestMeanReversionEntry:

    @pytest.mark.asyncio
    async def test_entry_when_zscore_below_threshold(self, strategy, market):
        """Genera BUY_YES cuando z_score < entry_zscore."""
        # Precios: SMA ≈ 0.50, último precio = 0.40 (z_score < -2.0)
        prices = [0.50] * 18 + [0.48, 0.42]
        fill_buffer(strategy, market, prices)

        tick = make_tick(yes_price=0.40, market_id=market.id)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.BUY_YES, f"Got {signal.type}: {signal.reason}"
        assert signal.source_strategy == "MeanReversion"
        assert signal.confidence > 0

    @pytest.mark.asyncio
    async def test_hold_when_zscore_above_threshold(self, strategy, market):
        """HOLD cuando z_score >= entry_zscore (no sobreventa)."""
        prices = [0.50] * 20  # SMA = 0.50, std ≈ 0 → z = 0
        fill_buffer(strategy, market, prices)

        tick = make_tick(yes_price=0.50, market_id=market.id)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.HOLD
        assert "z_score" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_when_buffer_insufficient(self, strategy, market):
        """HOLD cuando no hay suficientes ticks para SMA."""
        prices = [0.50] * 10  # < ma_window (20)
        fill_buffer(strategy, market, prices)

        tick = make_tick(yes_price=0.40, market_id=market.id)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.HOLD
        assert "buffer" in signal.reason or "ma_window" in signal.reason

    @pytest.mark.asyncio
    async def test_no_entry_when_in_position(self, strategy, market):
        """HOLD cuando ya hay posición abierta."""
        # Llenar buffer y poner en posición
        prices = [0.50] * 18 + [0.48, 0.42]
        fill_buffer(strategy, market, prices)

        state = strategy._get_or_create_state(market.id)
        state.record_entry(price=0.40)

        tick = make_tick(yes_price=0.38, market_id=market.id)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.HOLD
        assert "already_in_position" in signal.reason

    @pytest.mark.asyncio
    async def test_confidence_proportional_to_zscore(self, strategy, market):
        """Confidence = abs(z_score) / 4, clamp a [0, 1]."""
        prices = [0.50] * 20  # SMA = 0.50
        fill_buffer(strategy, market, prices)

        # z_score = -2.0 → confidence = 0.50
        make_tick(
            yes_price=0.443,  # necesita z ≈ -2.0, necesito std > 0
            market_id=market.id,
        )

        # Usamos un buffer con variabilidad para tener z_score < -2
        prices_var = [0.50] * 10 + [0.52, 0.48, 0.53, 0.47, 0.49, 0.51, 0.46, 0.54, 0.45, 0.55]
        fill_buffer(strategy, market, prices_var)

        tick_low = make_tick(yes_price=0.38, market_id=market.id)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick_low)

        signal = await strategy.should_enter(market, tick_low)
        if signal.type == SignalType.BUY_YES:
            # confidence debe ser abs(z_score) / 4
            z = strategy._get_or_create_state(market.id).extra.get("z_score", 0)
            expected = min(1.0, abs(z) / 4.0)
            assert abs(signal.confidence - expected) < 0.01, (
                f"Expected confidence ≈ {expected:.3f}, got {signal.confidence:.3f}"
            )

    @pytest.mark.asyncio
    async def test_spread_filter_blocks_entry(self, strategy, market):
        """Filtro de spread bloquea entrada con spread alto."""
        prices = [0.50] * 18 + [0.48, 0.42]
        fill_buffer(strategy, market, prices)

        tick = make_tick(yes_price=0.40, spread=0.05, market_id=market.id)  # > max_spread 0.03
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.HOLD
        assert "spread" in signal.reason.lower() or "SpreadFilter" in signal.reason

    @pytest.mark.asyncio
    async def test_liquidity_filter_blocks_entry(self, strategy, market):
        """Filtro de liquidez bloquea entrada con volumen bajo."""
        prices = [0.50] * 18 + [0.48, 0.42]
        fill_buffer(strategy, market, prices)

        tick = make_tick(yes_price=0.40, volume=500.0, market_id=market.id)  # < min_volume 1000
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert signal.type == SignalType.HOLD
        assert "volume" in signal.reason.lower() or "LiquidityFilter" in signal.reason


class TestMeanReversionExit:

    @pytest.mark.asyncio
    async def test_exit_when_zscore_returns_to_mean(self, strategy, market):
        """Genera EXIT cuando z_score > exit_zscore (retorno a media)."""
        # Precios con variabilidad para que std > 0 y z_score pueda > 0
        prices = [
            0.49, 0.48, 0.50, 0.47, 0.51, 0.46, 0.52, 0.45, 0.53, 0.44,
            0.50, 0.48, 0.49, 0.47, 0.51, 0.46, 0.50, 0.48, 0.49, 0.47,
        ]
        fill_buffer(strategy, market, prices)

        state = strategy._get_or_create_state(market.id)
        state.record_entry(price=0.38)  # Compró barato en sobreventa

        # Precio sube a 0.60 — claramente por encima de la SMA (~0.485)
        tick = make_tick(yes_price=0.60, market_id=market.id)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_exit(market, tick)
        assert signal.type == SignalType.EXIT, f"Got {signal.type}: {signal.reason}"
        assert "mean_reverted" in signal.reason

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_exit(self, strategy, market):
        """Genera EXIT cuando la pérdida supera stop_loss_pct (10%)."""
        prices = [0.50] * 20
        fill_buffer(strategy, market, prices)

        state = strategy._get_or_create_state(market.id)
        state.record_entry(price=0.50)  # Compró a 0.50

        # Cae 20% → > 10% stop loss
        tick = make_tick(yes_price=0.39, market_id=market.id)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_exit(market, tick)
        assert signal.type == SignalType.EXIT, f"Got {signal.type}: {signal.reason}"
        assert "stop_loss" in signal.reason

    @pytest.mark.asyncio
    async def test_timeout_triggers_exit(self, strategy, market):
        """Genera EXIT cuando la posición excede timeout_minutes."""
        prices = [0.50] * 20
        fill_buffer(strategy, market, prices)

        state = strategy._get_or_create_state(market.id)
        # Simula entrada hace 50 minutos (timeout es 45 min)
        state.record_entry(price=0.38)
        state.entry_at = datetime.utcnow() - timedelta(minutes=50)

        tick = make_tick(yes_price=0.40, market_id=market.id)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_exit(market, tick)
        assert signal.type == SignalType.EXIT, f"Got {signal.type}: {signal.reason}"
        assert "timeout" in signal.reason

    @pytest.mark.asyncio
    async def test_hold_when_no_open_position(self, strategy, market):
        """HOLD en should_exit cuando no hay posición abierta."""
        prices = [0.50] * 20
        fill_buffer(strategy, market, prices)

        tick = make_tick(yes_price=0.50, market_id=market.id)
        await strategy.on_cycle_start(market)
        await strategy.on_tick(market, tick)

        signal = await strategy.should_exit(market, tick)
        assert signal.type == SignalType.HOLD
        assert "no_open_position" in signal.reason


class TestMeanReversionConfig:

    def test_valid_config_passes(self):
        config = MeanReversionConfig()
        config.validate()  # no lanza

    def test_invalid_entry_zscore_raises(self):
        config = MeanReversionConfig(entry_zscore=1.0, exit_zscore=0.0)
        with pytest.raises(ValueError, match="entry_zscore"):
            config.validate()

    def test_negative_stop_loss_raises(self):
        config = MeanReversionConfig(stop_loss_pct=-0.10)
        with pytest.raises(ValueError, match="stop_loss_pct"):
            config.validate()

    def test_zero_timeout_raises(self):
        config = MeanReversionConfig(timeout_minutes=0)
        with pytest.raises(ValueError, match="timeout_minutes"):
            config.validate()

    def test_small_ma_window_raises(self):
        config = MeanReversionConfig(ma_window=2)
        with pytest.raises(ValueError, match="ma_window"):
            config.validate()
