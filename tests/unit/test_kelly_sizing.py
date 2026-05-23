# tests/unit/test_kelly_sizing.py
"""Tests unitarios para la regla de Kelly Criterion Position Sizing."""

from datetime import datetime

import pytest

from src.domain.enums.signal_type import SignalType
from src.domain.value_objects.signal import Signal
from src.risk.context import RiskContext
from src.risk.rules.kelly_sizing import KellySizingRule


def make_signal(confidence: float = 0.8) -> Signal:
    return Signal(
        type=SignalType.BUY_YES,
        market_id="test_market",
        confidence=confidence,
        source_strategy="TestStrategy",
        reason="test",
        timestamp=datetime.utcnow(),
    )


def make_context(**kwargs) -> RiskContext:
    defaults = dict(
        current_balance=1000.0,
        initial_day_balance=1000.0,
        open_positions_count=0,
        market_exposure_usdc=0.0,
        total_exposure_usdc=0.0,
        requested_amount=50.0,
        market_id="test_market",
        trading_mode="paper",
    )
    defaults.update(kwargs)
    return RiskContext(**defaults)


class TestKellySizingRule:
    """Tests para KellySizingRule: fórmula fraccional + guardrails."""

    @pytest.fixture
    def rule(self) -> KellySizingRule:
        return KellySizingRule(
            kelly_cap=0.25,
            safety_factor=0.25,
            target_price=0.90,
            position_floor=5.0,
            position_cap=50.0,
        )

    def test_rule_name_and_priority(self, rule):
        """Nombre y prioridad correctos."""
        assert rule.name == "KellySizingRule"
        assert rule.priority == 3

    # ── Edge positivo → Kelly sizing ──────────────────────────────────

    def test_sizes_with_high_confidence(self, rule):
        """
        Con edge positivo y confianza alta, Kelly sizing produce
        un monto significativo (mayor que el floor).
        """
        # market_price=0.5, confidence=0.8 → edge=0.3
        context = make_context(
            current_balance=1000.0,
            market_yes_price=0.5,
        )
        signal = make_signal(confidence=0.8)

        result = rule.evaluate(signal, context)
        assert result.allowed is True
        assert result.suggested_amount is not None
        # Kelly con edge=0.3, b=9, kelly_raw=(9*0.3 - 0.7)/9 = 0.222...
        # Con cap 0.25 y safety 0.25 → amount = 1000 * 0.222 * 0.25 = ~55.5
        # Pero position_cap = 50 → se capa a 50
        assert result.suggested_amount > 5.0  # Mayor que el floor
        assert result.suggested_amount <= 50.0  # Dentro del cap

    def test_sizes_more_with_higher_confidence(self, rule):
        """Mayor confianza → mayor Kelly size (proporcional al edge)."""
        context = make_context(
            current_balance=1000.0,
            market_yes_price=0.5,
        )

        low_signal = make_signal(confidence=0.6)   # edge=0.1
        high_signal = make_signal(confidence=0.9)  # edge=0.4

        low_result = rule.evaluate(low_signal, context)
        high_result = rule.evaluate(high_signal, context)

        assert low_result.allowed
        assert high_result.allowed
        assert low_result.suggested_amount is not None
        assert high_result.suggested_amount is not None
        assert high_result.suggested_amount >= low_result.suggested_amount

    # ── Edge cero o negativo → floor ──────────────────────────────────

    def test_low_confidence_uses_floor(self, rule):
        """
        Con edge <= 0 (confianza <= precio de mercado), usa el floor.
        """
        context = make_context(
            current_balance=1000.0,
            market_yes_price=0.6,
        )
        signal = make_signal(confidence=0.5)  # edge = -0.1

        result = rule.evaluate(signal, context)
        assert result.allowed is True
        assert result.suggested_amount == 5.0  # Floor

    def test_low_confidence_denies_if_balance_below_floor(self, rule):
        """
        Con edge <= 0 y balance insuficiente para el floor, deniega.
        """
        context = make_context(
            current_balance=4.0,  # Menor que floor
            market_yes_price=0.6,
        )
        signal = make_signal(confidence=0.5)

        result = rule.evaluate(signal, context)
        assert result.allowed is False
        assert "kelly_edge_zero" in result.reason

    # ── Kelly cap al 25% ──────────────────────────────────────────────

    def test_kelly_capped_at_cap(self, rule):
        """
        Con edge muy alto, el Kelly fraction se capa al 25%.
        """
        rule_low_cap = KellySizingRule(
            kelly_cap=0.10,  # Cap bajo
            safety_factor=0.25,
            target_price=0.90,
            position_floor=5.0,
            position_cap=50.0,
        )
        context = make_context(
            current_balance=1000.0,
            market_yes_price=0.2,  # Precio bajo → b alto → Kelly alto
        )
        signal = make_signal(confidence=0.99)  # Edge muy alto

        result = rule_low_cap.evaluate(signal, context)
        assert result.allowed is True
        # Con cap 0.10 y safety 0.25: max ~1000*0.10*0.25 = 25 USDC
        assert result.suggested_amount is not None
        assert result.suggested_amount <= 25.0

    # ── Floor y cap hardcoded ─────────────────────────────────────────

    def test_kelly_floor_at_min_position(self, rule):
        """
        El monto sugerido nunca baja del position_floor (salvo denegación por balance).
        """
        context = make_context(
            current_balance=1000.0,
            market_yes_price=0.55,
        )
        signal = make_signal(confidence=0.56)  # Edge muy pequeño

        result = rule.evaluate(signal, context)
        if result.allowed:
            assert result.suggested_amount is not None
            assert result.suggested_amount >= 5.0  # Floor

    def test_kelly_never_exceeds_position_cap(self, rule):
        """
        El monto sugerido nunca supera el position_cap (50 USDC).
        """
        context = make_context(
            current_balance=10000.0,  # Balance grande
            market_yes_price=0.1,
        )
        signal = make_signal(confidence=0.99)  # Edge enorme

        result = rule.evaluate(signal, context)
        assert result.allowed is True
        assert result.suggested_amount is not None
        assert result.suggested_amount <= 50.0  # Position cap

    def test_kelly_never_exceeds_requested_amount(self, rule):
        """
        Kelly NUNCA puede aumentar el position size por encima del configurado.
        """
        context = make_context(
            current_balance=1000.0,
            requested_amount=10.0,  # Config dice 10 USDC
            market_yes_price=0.3,
        )
        signal = make_signal(confidence=0.9)

        result = rule.evaluate(signal, context)
        assert result.allowed is True
        assert result.suggested_amount is not None
        assert result.suggested_amount <= 10.0  # Nunca supera el requested

    # ── Volatility dampener ────────────────────────────────────────────

    def test_volatility_dampener_reduces_size(self, rule):
        """
        Alta volatilidad → dampener < 1.0 → tamaño reducido.
        """
        context_high_vol = make_context(
            current_balance=1000.0,
            market_yes_price=0.5,
            recent_volatility=0.10,  # 10% volatilidad → dampener = 0.02/0.10 = 0.2
        )
        context_low_vol = make_context(
            current_balance=1000.0,
            market_yes_price=0.5,
            recent_volatility=0.01,  # 1% volatilidad → dampener = min(1.0, 0.02/0.01) = 1.0
        )
        signal = make_signal(confidence=0.8)

        high_result = rule.evaluate(signal, context_high_vol)
        low_result = rule.evaluate(signal, context_low_vol)

        assert high_result.allowed
        assert low_result.allowed
        # Con más volatilidad → menos capital expuesto
        assert high_result.suggested_amount is not None
        assert low_result.suggested_amount is not None
        assert high_result.suggested_amount < low_result.suggested_amount

    def test_no_volatility_data_uses_dampener_one(self, rule):
        """
        Sin datos de volatilidad (None), dampener = 1.0 → sin ajuste.
        """
        context = make_context(
            current_balance=1000.0,
            market_yes_price=0.5,
            recent_volatility=None,
        )
        signal = make_signal(confidence=0.8)

        result = rule.evaluate(signal, context)
        assert result.allowed is True
        assert result.suggested_amount is not None
        assert result.suggested_amount > 5.0

    def test_kelly_amount_exceeds_balance_denied(self, rule):
        """
        Si el monto Kelly supera el balance disponible, deniega.
        """
        context = make_context(
            current_balance=4.0,  # Muy poco balance
            market_yes_price=0.5,
        )
        signal = make_signal(confidence=0.9)

        result = rule.evaluate(signal, context)
        # Con balance=4, incluso el floor (5.0) excede → deny
        assert result.allowed is False
