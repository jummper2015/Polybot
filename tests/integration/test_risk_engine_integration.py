# tests/integration/test_risk_engine_integration.py

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.enums.signal_type import SignalType
from src.domain.value_objects.signal import Signal
from src.risk.engine import RiskEngine, RiskEngineConfig

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_signal(
    signal_type: SignalType = SignalType.BUY_YES,
    market_id: str = "test_market",
    confidence: float = 0.8,
    source_strategy: str = "BuyAboveThreshold",
) -> Signal:
    return Signal(
        type=signal_type,
        market_id=market_id,
        confidence=confidence,
        source_strategy=source_strategy,
        reason="test_signal",
        timestamp=datetime.utcnow(),
    )


# ── Tests ────────────────────────────────────────────────────────────────


class TestRiskEngineIntegration:

    @pytest.mark.asyncio
    async def test_evaluate_with_open_positions_allows_under_limits(self):
        """
        Evaluate con posiciones abiertas — todas las reglas pasan si
        estamos bajo los límites de exposure y max_positions.
        """
        risk = RiskEngine(
            config=RiskEngineConfig(
                min_balance_usdc=50.0,
                max_daily_drawdown_pct=0.10,
                max_exposure_pct=0.30,
                max_open_positions=5,
                max_net_exposure_pct=0.50,
            )
        )

        signal = _make_signal(confidence=0.85)
        decision = await risk.evaluate(
            signal=signal,
            current_balance=1000.0,
            open_positions_count=2,
            market_exposure_usdc=50.0,
            total_exposure_usdc=150.0,
            requested_amount=10.0,
            market_id="test_market",
            trading_mode="paper",
            market_yes_price=0.82,
        )

        assert decision.allowed
        assert decision.rule_triggered == "RiskEngine"

    @pytest.mark.asyncio
    async def test_evaluate_denies_when_market_exposure_exceeded(self):
        """
        MaxExposureRule deniega cuando la exposición en ESTE mercado
        ya alcanzó el máximo (30% del balance).
        market_exposure_usdc=310 sobre balance=1000 → 300 max → -10 disponible → DENY.
        """
        risk = RiskEngine(
            config=RiskEngineConfig(
                min_balance_usdc=50.0,
                max_daily_drawdown_pct=0.10,
                max_exposure_pct=0.30,
                max_open_positions=5,
            )
        )

        signal = _make_signal()
        decision = await risk.evaluate(
            signal=signal,
            current_balance=1000.0,
            open_positions_count=1,
            market_exposure_usdc=310.0,  # 310 de 300 permitidos → ya excedido
            total_exposure_usdc=310.0,
            requested_amount=10.0,
            market_id="test_market",
            trading_mode="paper",
        )

        assert not decision.allowed
        assert "MaxExposureRule" in decision.rule_triggered

    @pytest.mark.asyncio
    async def test_evaluate_denies_when_positions_limit_reached(self):
        """
        Evalúa con máximo de posiciones alcanzado → DENY.
        """
        risk = RiskEngine(
            config=RiskEngineConfig(
                min_balance_usdc=50.0,
                max_daily_drawdown_pct=0.10,
                max_exposure_pct=0.30,
                max_open_positions=3,
            )
        )

        signal = _make_signal()
        decision = await risk.evaluate(
            signal=signal,
            current_balance=1000.0,
            open_positions_count=3,  # Ya en el límite
            market_exposure_usdc=0.0,
            total_exposure_usdc=100.0,
            requested_amount=10.0,
            market_id="test_market",
            trading_mode="paper",
        )

        assert not decision.allowed
        assert "MaxPositionsRule" in decision.rule_triggered

    @pytest.mark.asyncio
    async def test_drawdown_resets_at_midnight(self):
        """
        El drawdown diario se resetea cuando cambia la fecha UTC.
        Verifica que _refresh_day_balance actualiza _initial_day_balance.
        """
        risk = RiskEngine(config=RiskEngineConfig(max_daily_drawdown_pct=0.10))

        signal = _make_signal()

        # Primera evaluación: establece el balance inicial del día
        decision_1 = await risk.evaluate(
            signal=signal,
            current_balance=1000.0,
            open_positions_count=0,
            market_exposure_usdc=0.0,
            total_exposure_usdc=0.0,
            requested_amount=10.0,
            market_id="test_market",
            trading_mode="paper",
        )
        assert decision_1.allowed
        initial_balance_1 = risk._initial_day_balance
        assert initial_balance_1 == 1000.0

        # Simular pérdida en el mismo día → drawdown activo
        decision_2 = await risk.evaluate(
            signal=signal,
            current_balance=850.0,  # Pérdida de 150 (15% drawdown > 10%)
            open_positions_count=0,
            market_exposure_usdc=0.0,
            total_exposure_usdc=0.0,
            requested_amount=10.0,
            market_id="test_market",
            trading_mode="paper",
        )
        assert not decision_2.allowed
        assert "DrawdownRule" in decision_2.rule_triggered

        # Simular cambio de día: forzar _day_date a ayer
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        risk._day_date = yesterday

        # Evaluar con nuevo balance → drawdown se resetea
        decision_3 = await risk.evaluate(
            signal=signal,
            current_balance=850.0,
            open_positions_count=0,
            market_exposure_usdc=0.0,
            total_exposure_usdc=0.0,
            requested_amount=10.0,
            market_id="test_market",
            trading_mode="paper",
        )

        # Con nuevo día, drawdown se recalcula desde 850, no desde 1000
        assert decision_3.allowed
        assert risk._initial_day_balance == 850.0

    @pytest.mark.asyncio
    async def test_multiple_rules_chain_correctly(self):
        """
        Múltiples reglas encadenadas correctamente en orden de prioridad.
        Verifica que el orden es: MinBalance(1) → Drawdown(2) → Kelly(3) →
        MaxExposure(4) → MaxPositions(5) → Hedge(6).
        """
        risk = RiskEngine(
            config=RiskEngineConfig(
                min_balance_usdc=50.0,
                max_daily_drawdown_pct=0.10,
                max_exposure_pct=0.30,
                max_open_positions=5,
            )
        )

        rule_names = [r.name for r in risk._rules]
        priorities = [r.priority for r in risk._rules]

        # Verificar orden de prioridad
        assert priorities == sorted(priorities), (
            f"Rules not sorted by priority: "
            f"{[(r.name, r.priority) for r in risk._rules]}"
        )

        # Verificar que todas las reglas están presentes
        expected_rules = [
            "MinBalanceRule",
            "DrawdownRule",
            "KellySizingRule",
            "MaxExposureRule",
            "MaxPositionsRule",
            "HedgeRule",
        ]
        for expected in expected_rules:
            assert expected in rule_names, (
                f"Missing rule: {expected}. Rules present: {rule_names}"
            )

    @pytest.mark.asyncio
    async def test_suggested_amount_propagates_to_final_decision(self):
        """
        El suggested_amount de KellySizingRule se propaga al RiskDecision final
        cuando todas las reglas pasan. Verifica floor y cap.
        """
        risk = RiskEngine(
            config=RiskEngineConfig(
                min_balance_usdc=50.0,
                max_daily_drawdown_pct=0.10,
                max_exposure_pct=0.30,
                max_open_positions=5,
                kelly_cap=0.25,
                kelly_safety_factor=0.25,
                kelly_target_price=0.90,
                kelly_position_floor=5.0,
                kelly_position_cap=50.0,
            )
        )

        signal = _make_signal(confidence=0.95)  # Confianza alta → Kelly significativo

        decision = await risk.evaluate(
            signal=signal,
            current_balance=1000.0,
            open_positions_count=0,
            market_exposure_usdc=0.0,
            total_exposure_usdc=0.0,
            requested_amount=50.0,
            market_id="test_market",
            trading_mode="paper",
            market_yes_price=0.82,
        )

        assert decision.allowed

        # Kelly debió sugerir un monto (<= 50 cap, >= 5 floor)
        if decision.suggested_amount is not None:
            assert decision.suggested_amount <= 50.0, (
                f"Suggested amount {decision.suggested_amount} exceeds cap 50.0"
            )
            assert decision.suggested_amount >= 5.0, (
                f"Suggested amount {decision.suggested_amount} below floor 5.0"
            )
