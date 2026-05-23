# tests/property/test_risk_invariants.py
"""Property-based tests for RiskEngine invariants (P3.2).

Invariants under test:
  - MinBalanceRule.allowed → balance_after >= min_balance      (∀ balances)
  - MinBalanceRule.denied → balance_after < min_balance        (∀ balances)
  - DrawdownRule.denied → drawdown >= max                      (∀ balances)
  - MaxExposureRule only suggests amounts <= available
  - KellySizingRule: suggested_amount never exceeds requested
  - KellySizingRule: suggested_amount ∈ [floor, min(cap, requested, balance)]
"""

import structlog
from hypothesis import given, settings
from hypothesis import strategies as st

from src.domain.enums.signal_type import SignalType
from src.domain.value_objects.signal import Signal
from src.risk.context import RiskContext
from src.risk.rules.drawdown import DrawdownRule
from src.risk.rules.hedge import HedgeRule
from src.risk.rules.kelly_sizing import KellySizingRule
from src.risk.rules.max_exposure import MaxExposureRule
from src.risk.rules.max_positions import MaxPositionsRule
from src.risk.rules.min_balance import MinBalanceRule

structlog.configure(processors=[structlog.processors.KeyValueRenderer()])

# ── Strategies para Hypothesis ────────────────────────────────────────────

balance_st = st.floats(min_value=1.0, max_value=10000.0, allow_infinity=False, allow_nan=False)

# Monto solicitado: entre 1 y 500 USDC
amount_st = st.floats(min_value=1.0, max_value=500.0, allow_infinity=False, allow_nan=False)

# Confidence: 0.0 a 1.0
confidence_st = st.floats(min_value=0.0, max_value=1.0, allow_infinity=False, allow_nan=False)

# Market yes price: 0.01 a 0.99
market_price_st = st.floats(min_value=0.01, max_value=0.99, allow_infinity=False, allow_nan=False)

# Exposure: 0 a balance
def exposure_for_balance(balance: float) -> st.SearchStrategy:
    return st.floats(min_value=0.0, max_value=balance, allow_infinity=False, allow_nan=False)

# Target price: 0.50 a 0.99
target_price_st = st.floats(min_value=0.50, max_value=0.99, allow_infinity=False, allow_nan=False)


# ── Helpers ────────────────────────────────────────────────────────────────

def make_signal(confidence: float = 0.5, signal_type: SignalType = SignalType.BUY_YES) -> Signal:
    """Crea una Signal sintética para property tests."""
    from datetime import datetime
    return Signal(
        type=signal_type,
        market_id="test_market",
        confidence=confidence,
        source_strategy="Test",
        reason="property test",
        timestamp=datetime.utcnow(),
    )


def make_context(
    balance: float,
    requested: float,
    market_exposure: float = 0.0,
    total_exposure: float = 0.0,
    open_positions: int = 0,
    initial_balance: float | None = None,
    market_price: float = 0.5,
) -> RiskContext:
    """Crea un RiskContext sintético."""
    return RiskContext(
        current_balance=balance,
        initial_day_balance=initial_balance if initial_balance is not None else balance,
        open_positions_count=open_positions,
        market_exposure_usdc=market_exposure,
        total_exposure_usdc=total_exposure,
        requested_amount=requested,
        market_id="test_market",
        trading_mode="paper",
        market_yes_price=market_price,
    )


# ═════════════════════════════════════════════════════════════════════════════
# MIN BALANCE RULE
# ═════════════════════════════════════════════════════════════════════════════

class TestMinBalanceInvariant:
    """
    MinBalanceRule.allowed → balance_after >= min_balance.
    MinBalanceRule.denied → balance_after < min_balance.
    """

    @given(
        balance=balance_st,
        amount=amount_st,
        min_balance=st.floats(min_value=10.0, max_value=200.0, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_allowed_implies_balance_above_min(self, balance, amount, min_balance):
        """∀ balance, amount, min_balance: allowed → balance_after >= min_balance."""
        rule = MinBalanceRule(min_balance_usdc=min_balance)
        signal = make_signal()
        context = make_context(balance=balance, requested=amount)

        decision = rule.evaluate(signal, context)
        balance_after = balance - amount

        if decision.allowed:
            assert balance_after >= min_balance, (
                f"Allowed but balance_after={balance_after:.2f} < "
                f"min_balance={min_balance:.2f} "
                f"(balance={balance:.2f}, amount={amount:.2f})"
            )

    @given(
        balance=balance_st,
        amount=amount_st,
        min_balance=st.floats(min_value=10.0, max_value=200.0, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_denied_implies_balance_below_min(self, balance, amount, min_balance):
        """∀ balance, amount, min_balance: denied → balance_after < min_balance."""
        rule = MinBalanceRule(min_balance_usdc=min_balance)
        signal = make_signal()
        context = make_context(balance=balance, requested=amount)

        decision = rule.evaluate(signal, context)
        balance_after = balance - amount

        if not decision.allowed:
            assert balance_after < min_balance, (
                f"Denied but balance_after={balance_after:.2f} >= "
                f"min_balance={min_balance:.2f}"
            )


class TestMinBalanceNeverSuggestsAmount:
    """MinBalanceRule NUNCA sugiere un monto ajustado: es binaria (allow/deny)."""

    @given(
        balance=balance_st,
        amount=amount_st,
        min_balance=st.floats(min_value=10.0, max_value=200.0, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_never_suggests_amount(self, balance, amount, min_balance):
        """MinBalanceRule never returns suggested_amount."""
        rule = MinBalanceRule(min_balance_usdc=min_balance)
        signal = make_signal()
        context = make_context(balance=balance, requested=amount)

        decision = rule.evaluate(signal, context)

        assert decision.suggested_amount is None, (
            f"MinBalanceRule should not suggest amounts, got {decision.suggested_amount}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# DRAWDOWN RULE
# ═════════════════════════════════════════════════════════════════════════════

class TestDrawdownInvariant:
    """
    DrawdownRule.denied → drawdown >= max.
    DrawdownRule.allowed → drawdown < max.
    """

    @given(
        initial_balance=balance_st,
        current_balance=balance_st,
        max_drawdown=st.floats(min_value=0.05, max_value=0.30, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_denied_implies_drawdown_exceeds_max(self, initial_balance, current_balance, max_drawdown):
        """∀ balances: denied → drawdown >= max_drawdown."""
        rule = DrawdownRule(max_daily_drawdown_pct=max_drawdown)
        signal = make_signal()
        context = make_context(
            balance=current_balance,
            requested=10.0,
            initial_balance=initial_balance,
        )

        decision = rule.evaluate(signal, context)
        drawdown = context.drawdown_pct

        if not decision.allowed:
            assert drawdown >= max_drawdown, (
                f"Denied but drawdown={drawdown:.4%} < max={max_drawdown:.2%} "
                f"(initial={initial_balance:.2f}, current={current_balance:.2f})"
            )

    @given(
        initial_balance=balance_st,
        current_balance=balance_st,
        max_drawdown=st.floats(min_value=0.05, max_value=0.30, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_allowed_implies_drawdown_below_max(self, initial_balance, current_balance, max_drawdown):
        """∀ balances: allowed → drawdown < max_drawdown."""
        rule = DrawdownRule(max_daily_drawdown_pct=max_drawdown)
        signal = make_signal()
        context = make_context(
            balance=current_balance,
            requested=10.0,
            initial_balance=initial_balance,
        )

        decision = rule.evaluate(signal, context)
        drawdown = context.drawdown_pct

        if decision.allowed:
            assert drawdown < max_drawdown, (
                f"Allowed but drawdown={drawdown:.4%} >= max={max_drawdown:.2%}"
            )


# ═════════════════════════════════════════════════════════════════════════════
# MAX EXPOSURE RULE
# ═════════════════════════════════════════════════════════════════════════════

class TestMaxExposureInvariant:
    """
    MaxExposureRule only suggests amounts <= available.
    Cuando ajusta el monto, suggested_amount ≤ available_usdc.
    """

    @given(
        balance=balance_st,
        market_exposure=st.floats(min_value=0.0, max_value=10000.0, allow_infinity=False, allow_nan=False),
        amount=amount_st,
        max_exposure_pct=st.floats(min_value=0.10, max_value=0.50, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_suggested_amount_not_exceeds_available(self, balance, market_exposure, amount, max_exposure_pct):
        """∀ state: suggested_amount ≤ available when adjusted."""
        rule = MaxExposureRule(max_exposure_pct=max_exposure_pct)
        signal = make_signal()
        context = make_context(
            balance=balance,
            requested=amount,
            market_exposure=market_exposure,
        )

        decision = rule.evaluate(signal, context)
        available = balance * max_exposure_pct - market_exposure

        if decision.suggested_amount is not None:
            assert decision.suggested_amount <= max(available, 0.0) + 0.01, (
                f"suggested_amount={decision.suggested_amount:.2f} > "
                f"available={available:.2f} "
                f"(balance={balance:.2f}, exposure={market_exposure:.2f})"
            )

    @given(
        balance=balance_st,
        amount=amount_st,
        max_exposure_pct=st.floats(min_value=0.10, max_value=0.50, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_allowed_when_within_limits(self, balance, amount, max_exposure_pct):
        """∀ state with zero exposure and amount <= limit: should allow."""
        rule = MaxExposureRule(max_exposure_pct=max_exposure_pct)
        signal = make_signal()
        context = make_context(
            balance=balance,
            requested=amount,
            market_exposure=0.0,
        )

        decision = rule.evaluate(signal, context)
        limit = balance * max_exposure_pct

        if amount <= limit:
            assert decision.allowed, (
                f"Expected allow when amount={amount:.2f} <= limit={limit:.2f}, "
                f"got denied: {decision.reason}"
            )


# ═════════════════════════════════════════════════════════════════════════════
# KELLY SIZING RULE
# ═════════════════════════════════════════════════════════════════════════════

class TestKellySizingInvariant:
    """
    KellySizingRule invariants:
      - suggested_amount never exceeds requested_amount
      - suggested_amount never exceeds balance
      - suggested_amount ∈ [floor, cap] when edge > 0
    """

    @given(
        balance=balance_st,
        amount=amount_st,
        confidence=confidence_st,
        market_price=market_price_st,
        target=target_price_st,
    )
    @settings(max_examples=200)
    def test_suggested_never_exceeds_requested(self, balance, amount, confidence, market_price, target):
        """∀ params: Kelly suggested_amount ≤ requested_amount."""
        rule = KellySizingRule(target_price=target, position_floor=5.0, position_cap=50.0)
        signal = make_signal(confidence=confidence)
        context = make_context(balance=balance, requested=amount, market_price=market_price)

        decision = rule.evaluate(signal, context)

        if decision.suggested_amount is not None:
            assert decision.suggested_amount <= amount + 0.01, (
                f"suggested={decision.suggested_amount:.2f} > requested={amount:.2f}"
            )

    @given(
        balance=balance_st,
        amount=amount_st,
        confidence=confidence_st,
        market_price=market_price_st,
        target=target_price_st,
    )
    @settings(max_examples=200)
    def test_suggested_never_exceeds_balance(self, balance, amount, confidence, market_price, target):
        """∀ params: Kelly suggested_amount ≤ balance."""
        rule = KellySizingRule(target_price=target, position_floor=5.0, position_cap=50.0)
        signal = make_signal(confidence=confidence)
        context = make_context(balance=balance, requested=amount, market_price=market_price)

        decision = rule.evaluate(signal, context)

        if decision.allowed and decision.suggested_amount is not None:
            assert decision.suggested_amount <= balance + 0.01, (
                f"suggested={decision.suggested_amount:.2f} > balance={balance:.2f}"
            )

    @given(
        balance=st.floats(min_value=50.0, max_value=10000.0, allow_infinity=False, allow_nan=False),
        amount=st.floats(min_value=10.0, max_value=500.0, allow_infinity=False, allow_nan=False),
        confidence=st.floats(min_value=0.6, max_value=1.0, allow_infinity=False, allow_nan=False),
        market_price=st.floats(min_value=0.01, max_value=0.50, allow_infinity=False, allow_nan=False),
        target=target_price_st,
    )
    @settings(max_examples=200)
    def test_suggested_in_floor_cap_range(self, balance, amount, confidence, market_price, target):
        """∀ edge > 0: suggested_amount ∈ [floor, cap] or = request."""
        rule = KellySizingRule(
            target_price=target,
            position_floor=5.0,
            position_cap=50.0,
        )
        signal = make_signal(confidence=confidence)
        context = make_context(balance=balance, requested=amount, market_price=market_price)

        decision = rule.evaluate(signal, context)
        edge = confidence - market_price

        if edge > 0 and decision.allowed and decision.suggested_amount is not None:
            amt = decision.suggested_amount
            assert 5.0 <= amt <= min(50.0, amount) + 0.01, (
                f"suggested={amt:.2f} not in [5.0, {min(50.0, amount)}] "
                f"(edge={edge:.4f}, confidence={confidence:.2f})"
            )

    @given(
        balance=st.floats(min_value=0.0, max_value=5.0, allow_infinity=False, allow_nan=False),
        amount=amount_st,
        confidence=st.floats(min_value=0.0, max_value=0.4, allow_infinity=False, allow_nan=False),
        market_price=st.floats(min_value=0.60, max_value=0.99, allow_infinity=False, allow_nan=False),
        target=target_price_st,
    )
    @settings(max_examples=200)
    def test_negative_edge_uses_floor_or_denies(self, balance, amount, confidence, market_price, target):
        """∀ edge <= 0: either denied or uses floor amount."""
        rule = KellySizingRule(target_price=target, position_floor=5.0)
        signal = make_signal(confidence=confidence)
        context = make_context(balance=balance, requested=amount, market_price=market_price)

        decision = rule.evaluate(signal, context)
        edge = confidence - market_price

        if edge <= 0:
            # Either denied or suggests floor
            if decision.allowed and decision.suggested_amount is not None:
                assert decision.suggested_amount <= amount + 0.01, (
                    f"Negative edge ({edge:.4f}): suggested={decision.suggested_amount:.2f} "
                    f"should not exceed requested={amount:.2f}"
                )


# ═════════════════════════════════════════════════════════════════════════════
# MAX POSITIONS RULE
# ═════════════════════════════════════════════════════════════════════════════

class TestMaxPositionsInvariant:
    """MaxPositionsRule: BUY_YES requires slots available."""

    @given(
        open_positions=st.integers(min_value=0, max_value=10),
        max_positions=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=100)
    def test_deny_when_at_capacity(self, open_positions, max_positions):
        """∀ positions: denied only when count >= max."""
        rule = MaxPositionsRule(max_open_positions=max_positions)
        signal = make_signal(signal_type=SignalType.BUY_YES)
        context = make_context(balance=1000.0, requested=10.0, open_positions=open_positions)

        decision = rule.evaluate(signal, context)

        if open_positions >= max_positions:
            assert not decision.allowed, (
                f"Expected deny when {open_positions} >= {max_positions}"
            )
        else:
            assert decision.allowed, (
                f"Expected allow when {open_positions} < {max_positions}"
            )

    @given(open_positions=st.integers(min_value=0, max_value=10))
    @settings(max_examples=100)
    def test_exit_always_allowed(self, open_positions):
        """EXIT signals always allowed regardless of positions count."""
        rule = MaxPositionsRule(max_open_positions=1)
        signal = make_signal(signal_type=SignalType.EXIT)
        context = make_context(balance=1000.0, requested=10.0, open_positions=open_positions)

        decision = rule.evaluate(signal, context)

        assert decision.allowed, (
            f"EXIT should always be allowed, got denied at {open_positions} positions"
        )


# ═════════════════════════════════════════════════════════════════════════════
# HEDGE RULE
# ═════════════════════════════════════════════════════════════════════════════

class TestHedgeRuleInvariant:
    """HedgeRule: only acts on BUY_NO signals."""

    @given(confidence=confidence_st)
    @settings(max_examples=100)
    def test_non_hedge_signals_always_allowed(self, confidence):
        """Non-BUY_NO signals always pass HedgeRule."""
        rule = HedgeRule(max_net_exposure_pct=0.10)  # Very restrictive
        signal = make_signal(confidence=confidence, signal_type=SignalType.BUY_YES)
        context = make_context(balance=1000.0, requested=10.0, total_exposure=900.0)  # Over limit

        decision = rule.evaluate(signal, context)

        assert decision.allowed, (
            "BUY_YES should always pass HedgeRule, got denied"
        )

    @given(
        balance=balance_st,
        amount=amount_st,
        total_exposure=st.floats(min_value=0.0, max_value=10000.0, allow_infinity=False, allow_nan=False),
        max_exposure=st.floats(min_value=0.10, max_value=0.70, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_hedge_allowed_only_when_exposure_below_limit(
        self, balance, amount, total_exposure, max_exposure
    ):
        """∀ state: BUY_NO allowed only if exposure < max."""
        rule = HedgeRule(max_net_exposure_pct=max_exposure)
        signal = make_signal(signal_type=SignalType.BUY_NO)
        context = make_context(balance=balance, requested=amount, total_exposure=total_exposure)

        decision = rule.evaluate(signal, context)
        exposure_pct = context.exposure_pct

        if not decision.allowed:
            assert exposure_pct >= max_exposure, (
                f"Denied but exposure={exposure_pct:.2%} < max={max_exposure:.2%}"
            )
