# tests/e2e/test_full_cycle.py

from datetime import datetime, timedelta

import pytest

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.signal_type import SignalType
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.risk.engine import RiskEngine, RiskEngineConfig
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.engine import StrategyEngine


def make_market() -> Market:
    return Market(
        id="e2e_market",
        asset=Asset.BTC,
        window=Window.M5,
        question="E2E Test Market",
        status=MarketStatus.ACTIVE,
        yes_token_id="yes_token",
        no_token_id="no_token",
        yes_price=0.76,
        no_price=0.24,
        volume_24h=5000.0,
        expiry=datetime.utcnow() + timedelta(hours=1),
    )


def make_tick(yes_price: float) -> MarketTick:
    spread = 0.01
    return MarketTick(
        market_id="e2e_market",
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=yes_price - spread/2,
        best_ask=yes_price + spread/2,
        spread=spread,
        volume_24h=5000.0,
        timestamp=datetime.utcnow().replace(hour=12),
    )


class TestFullTradingCycle:
    """
    Tests end-to-end que verifican el flujo completo:
    Tick → Strategy → Risk → Signal
    Sin DB ni Redis — todo mockeado.
    """

    @pytest.mark.asyncio
    async def test_full_entry_cycle_generates_buy_signal(self):
        """
        Flujo completo: N ticks sobre threshold → BUY_YES
        verificado por Risk Engine → ALLOW.
        """
        config   = BuyAboveThresholdConfig(
            threshold=0.75, required_ticks=3,
            stop_loss_pct=0.15, target_price=0.90,
        )
        strategy = BuyAboveThresholdStrategy(config=config)
        engine   = StrategyEngine(strategies=[strategy])
        market   = make_market()
        tick     = make_tick(yes_price=0.82)

        await engine.on_cycle_start(market)
        for _ in range(3):
            await engine.on_tick(market, tick)

        signal = await engine.should_enter(market, tick)
        assert signal.type == SignalType.BUY_YES
        assert signal.confidence > 0.0
        assert signal.source_strategy == "BuyAboveThreshold"

    @pytest.mark.asyncio
    async def test_risk_engine_allows_valid_signal(self):
        """El RiskEngine permite señales válidas con portfolio sano."""
        risk   = RiskEngine(config=RiskEngineConfig(
            min_balance_usdc=50.0,
            max_daily_drawdown_pct=0.10,
            max_exposure_pct=0.30,
            max_open_positions=5,
        ))

        from src.domain.enums.signal_type import SignalType
        from src.domain.value_objects.signal import Signal
        signal = Signal(
            type=SignalType.BUY_YES,
            market_id="e2e_market",
            confidence=0.8,
            source_strategy="BuyAboveThreshold",
            reason="test",
            timestamp=datetime.utcnow(),
        )

        decision = await risk.evaluate(
            signal=signal,
            current_balance=1000.0,
            open_positions_count=0,
            market_exposure_usdc=0.0,
            total_exposure_usdc=0.0,
            requested_amount=10.0,
            market_id="e2e_market",
            trading_mode="paper",
        )

        assert decision.allowed
        assert decision.rule_triggered == "RiskEngine"

    @pytest.mark.asyncio
    async def test_risk_engine_denies_when_balance_too_low(self):
        """El RiskEngine deniega cuando el balance es insuficiente."""
        risk   = RiskEngine(config=RiskEngineConfig(min_balance_usdc=50.0))

        from src.domain.enums.signal_type import SignalType
        from src.domain.value_objects.signal import Signal
        signal = Signal(
            type=SignalType.BUY_YES, market_id="e2e_market",
            confidence=0.8, source_strategy="test",
            reason="test", timestamp=datetime.utcnow(),
        )

        decision = await risk.evaluate(
            signal=signal,
            current_balance=55.0,     # 55 - 10 = 45 < min_balance 50
            open_positions_count=0,
            market_exposure_usdc=0.0,
            total_exposure_usdc=0.0,
            requested_amount=10.0,
            market_id="e2e_market",
            trading_mode="paper",
        )

        assert not decision.allowed
        assert "MinBalanceRule" in decision.rule_triggered

    @pytest.mark.asyncio
    async def test_strategy_engine_marks_entry_correctly(self):
        """mark_entry actualiza el estado in_position correctamente."""
        config   = BuyAboveThresholdConfig(threshold=0.75, required_ticks=3)
        strategy = BuyAboveThresholdStrategy(config=config)
        engine   = StrategyEngine(strategies=[strategy])
        market   = make_market()

        await engine.on_cycle_start(market)
        await engine.on_tick(market, make_tick(0.80))

        # Estado inicial: sin posición
        state = engine.get_state("BuyAboveThreshold", "e2e_market")
        assert state is not None
        assert not state.in_position

        # Simula ejecución exitosa
        engine.mark_entry("BuyAboveThreshold", "e2e_market", price=0.81)
        assert state.in_position
        assert state.entry_price == 0.81

        # Simula cierre
        engine.mark_exit("BuyAboveThreshold", "e2e_market")
        assert not state.in_position
        assert state.entry_price is None
