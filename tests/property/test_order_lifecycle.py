# tests/property/test_order_lifecycle.py
"""Property-based tests for Order lifecycle invariants (P3.2).

Invariants under test:
  - Order transitions: mark_filled → status=FILLED, fill_price > 0
  - Filled order always has fill_price > 0 and filled_at is not None
  - Failed order always has error message set
  - Shares calculation: shares = amount / fill_price when filled
  - Shares is None when not filled
  - mark_failed → status=FAILED
  - R2.5.1: Idempotency key determinism, side/operation collision prevention
"""

import hashlib
from datetime import datetime, timezone

import structlog
from hypothesis import given, settings
from hypothesis import strategies as st

from src.domain.entities.order import Order
from src.domain.enums.order_side import OrderSide
from src.domain.enums.order_status import OrderStatus
from src.domain.enums.trading_mode import TradingMode
from src.execution.real_handler import RealTradingHandler

structlog.configure(processors=[structlog.processors.KeyValueRenderer()])

# ── Strategies para Hypothesis ────────────────────────────────────────────

amount_st = st.floats(min_value=1.0, max_value=1000.0, allow_infinity=False, allow_nan=False)
fill_price_st = st.floats(min_value=0.01, max_value=0.99, allow_infinity=False, allow_nan=False)
slippage_st = st.floats(min_value=-0.05, max_value=0.05, allow_infinity=False, allow_nan=False)
target_price_st = st.floats(min_value=0.01, max_value=0.99, allow_infinity=False, allow_nan=False)
order_id_st = st.text(min_size=8, max_size=32, alphabet="abcdef0123456789")
market_id_st = st.text(min_size=8, max_size=32, alphabet="abcdef0123456789")
reason_st = st.text(min_size=1, max_size=100)
error_st = st.text(min_size=1, max_size=200)
strategy_name_st = st.sampled_from(["BuyAboveThreshold", "MeanReversion", "CustomStrategy"])

# ── Helpers ────────────────────────────────────────────────────────────────

def make_order(
    order_id: str = "test_order_001",
    amount: float = 10.0,
    target_price: float = 0.60,
    side: OrderSide = OrderSide.YES,
    status: OrderStatus = OrderStatus.PENDING,
    mode: TradingMode = TradingMode.PAPER,
    strategy: str = "Test",
    reason: str = "test",
    market_id: str = "test_market",
) -> Order:
    """Crea una Order sintética para property tests."""
    return Order(
        id=order_id,
        market_id=market_id,
        side=side,
        amount=amount,
        target_price=target_price,
        fill_price=None,
        slippage=None,
        status=status,
        mode=mode,
        strategy=strategy,
        reason=reason,
        created_at=datetime.utcnow(),
    )


# ═════════════════════════════════════════════════════════════════════════════
# ORDER TRANSITIONS
# ═════════════════════════════════════════════════════════════════════════════

class TestOrderMarkFilledInvariants:
    """
    mark_filled → status=FILLED, fill_price > 0, filled_at is not None.
    """

    @given(
        amount=amount_st,
        target_price=target_price_st,
        fill_price=fill_price_st,
        slippage=slippage_st,
        order_id=order_id_st,
        market_id=market_id_st,
        strategy=strategy_name_st,
        reason=reason_st,
    )
    @settings(max_examples=200)
    def test_mark_filled_sets_status_and_price(self, amount, target_price, fill_price, slippage, order_id, market_id, strategy, reason):
        """∀ valid params: mark_filled → status=FILLED, fill_price set, filled_at set."""
        order = Order(
            id=order_id,
            market_id=market_id,
            side=OrderSide.YES,
            amount=amount,
            target_price=target_price,
            fill_price=None,
            slippage=None,
            status=OrderStatus.PENDING,
            mode=TradingMode.PAPER,
            strategy=strategy,
            reason=reason,
            created_at=datetime.utcnow(),
        )

        order.mark_filled(fill_price=fill_price, slippage=slippage)

        assert order.status == OrderStatus.FILLED, (
            f"Expected FILLED after mark_filled, got {order.status.value}"
        )
        assert order.fill_price == fill_price, (
            f"fill_price mismatch: {order.fill_price} != {fill_price}"
        )
        assert order.fill_price > 0, (
            f"fill_price={order.fill_price} should be > 0"
        )
        assert order.filled_at is not None, "filled_at should be set"
        assert order.slippage == slippage, (
            f"slippage mismatch: {order.slippage} != {slippage}"
        )

    @given(
        amount=amount_st,
        target_price=target_price_st,
        fill_price=fill_price_st,
        slippage=slippage_st,
    )
    @settings(max_examples=200)
    def test_shares_correct_after_fill(self, amount, target_price, fill_price, slippage):
        """∀ valid params: shares = amount / fill_price after mark_filled."""
        order = make_order(amount=amount, target_price=target_price)
        order.mark_filled(fill_price=fill_price, slippage=slippage)

        expected_shares = amount / fill_price if fill_price > 0 else None
        actual_shares = order.shares

        if expected_shares is not None:
            assert actual_shares is not None, "shares should not be None after fill"
            assert abs(actual_shares - expected_shares) < 0.01, (
                f"shares={actual_shares:.4f} != expected={expected_shares:.4f} "
                f"(amount={amount}, fill_price={fill_price})"
            )


class TestOrderMarkFailedInvariants:
    """
    mark_failed → status=FAILED, error is not None/empty.
    """

    @given(
        amount=amount_st,
        target_price=target_price_st,
        error_msg=error_st,
        order_id=order_id_st,
        market_id=market_id_st,
        strategy=strategy_name_st,
        reason=reason_st,
    )
    @settings(max_examples=200)
    def test_mark_failed_sets_status_and_error(self, amount, target_price, error_msg, order_id, market_id, strategy, reason):
        """∀ valid params: mark_failed → status=FAILED, error set."""
        order = Order(
            id=order_id,
            market_id=market_id,
            side=OrderSide.YES,
            amount=amount,
            target_price=target_price,
            fill_price=None,
            slippage=None,
            status=OrderStatus.PENDING,
            mode=TradingMode.PAPER,
            strategy=strategy,
            reason=reason,
            created_at=datetime.utcnow(),
        )

        order.mark_failed(error=error_msg)

        assert order.status == OrderStatus.FAILED, (
            f"Expected FAILED after mark_failed, got {order.status.value}"
        )
        assert order.error == error_msg, (
            f"error mismatch: '{order.error}' != '{error_msg}'"
        )
        assert order.error, "error should be non-empty after mark_failed"

    @given(
        amount=amount_st,
        target_price=target_price_st,
        error_msg=error_st,
    )
    @settings(max_examples=200)
    def test_shares_none_after_fail(self, amount, target_price, error_msg):
        """∀ valid params: shares is None after mark_failed."""
        order = make_order(amount=amount, target_price=target_price)
        order.mark_failed(error=error_msg)

        assert order.shares is None, (
            f"shares should be None after failed order, got {order.shares}"
        )
        assert order.fill_price is None, (
            "fill_price should be None after failed order"
        )


class TestOrderSharesInvariant:
    """
    Invariantes del cálculo de shares.
    """

    @given(
        amount=amount_st,
        fill_price=fill_price_st,
    )
    @settings(max_examples=200)
    def test_shares_none_when_pending(self, amount, fill_price):
        """shares is None when order is still PENDING (no fill_price)."""
        order = Order(
            id="test",
            market_id="test",
            side=OrderSide.YES,
            amount=amount,
            target_price=fill_price,
            fill_price=None,
            slippage=None,
            status=OrderStatus.PENDING,
            mode=TradingMode.PAPER,
            strategy="Test",
            reason="test",
            created_at=datetime.utcnow(),
        )

        assert order.shares is None, (
            f"shares should be None for PENDING order, got {order.shares}"
        )

    @given(
        amount=amount_st,
        fill_price=st.floats(min_value=0.01, max_value=0.99, allow_infinity=False, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_shares_formula(self, amount, fill_price):
        """shares = amount / fill_price when filled."""
        order = make_order(amount=amount, target_price=fill_price)
        order.mark_filled(fill_price=fill_price, slippage=0.0)

        expected = amount / fill_price
        assert order.shares is not None
        assert abs(order.shares - expected) < 0.01, (
            f"shares={order.shares:.4f} != amount/fill_price={expected:.4f}"
        )


class TestOrderImmutabilityAfterTransition:
    """
    Una vez que una orden está en estado terminal, los campos críticos
    deben preservarse o actualizarse de forma coherente.
    """

    @given(
        amount=amount_st,
        target_price=target_price_st,
        fill_price=fill_price_st,
        error_msg=error_st,
    )
    @settings(max_examples=200)
    def test_filled_preserves_fill_price_after_mark_failed(self, amount, target_price, fill_price, error_msg):
        """After mark_filled → mark_failed, error is set but fill data remains."""
        order = make_order(amount=amount, target_price=target_price)
        order.mark_filled(fill_price=fill_price, slippage=0.0)
        original_fill_price = order.fill_price
        original_filled_at = order.filled_at

        # After filling, attempt to mark_failed
        order.mark_failed(error=error_msg)

        # Status changes to FAILED but fill data remains (audit trail)
        assert order.fill_price == original_fill_price, (
            "fill_price should be preserved after mark_failed"
        )
        assert order.filled_at == original_filled_at, (
            "filled_at should be preserved after mark_failed"
        )
        assert order.error == error_msg, (
            "error should be set after mark_failed"
        )

    @given(
        amount=amount_st,
        target_price=target_price_st,
        fill_price=fill_price_st,
        error_msg=error_st,
    )
    @settings(max_examples=200)
    def test_failed_preserves_error_after_mark_filled(self, amount, target_price, fill_price, error_msg):
        """After mark_failed → mark_filled, the order transitions to FILLED."""
        order = make_order(amount=amount, target_price=target_price)
        order.mark_failed(error=error_msg)

        # After failing, attempt to mark_filled
        order.mark_filled(fill_price=fill_price, slippage=0.0)

        # Last transition wins — order is now FILLED
        assert order.status == OrderStatus.FILLED, (
            f"Expected FILLED after mark_filled on FAILED order, got {order.status.value}"
        )
        assert order.fill_price == fill_price, (
            "fill_price should be updated after mark_filled"
        )


class TestIdempotencyKeyPropertyInvariants:
    """R2.5.1: Property-based invariants for RealTradingHandler._generate_idempotency_key.

    Invariants under test:
      - Same inputs = same key (determinism, essential for idempotency)
      - Different side (YES vs NO) = different key (no collision entry/hedge)
      - Different operation (entry/exit/hedge/redeem) = different key
      - Same side+operation but different strategy = different key
    """

    side_st = st.sampled_from(["YES", "NO", "BUY_YES", "BUY_NO"])
    operation_st = st.sampled_from(["entry", "exit", "hedge", "redeem"])

    @given(
        strategy=st.sampled_from(
            ["BuyAboveThreshold", "MeanReversion", "CustomStrategy"]
        ),
        market_id=market_id_st,
        side=side_st,
        operation=operation_st,
    )
    @settings(max_examples=200, deadline=None)
    def test_same_inputs_produce_same_key(
        self, strategy, market_id, side, operation
    ):
        """∀ (strategy, market, side, operation): same inputs → same key."""
        key1 = RealTradingHandler._generate_idempotency_key(
            strategy_name=strategy,
            market_id=market_id,
            side=side,
            operation=operation,
        )
        key2 = RealTradingHandler._generate_idempotency_key(
            strategy_name=strategy,
            market_id=market_id,
            side=side,
            operation=operation,
        )

        assert key1 == key2, (
            f"R2.5.1: Same inputs produce different keys: "
            f"{key1} != {key2} "
            f"(s={strategy}, m={market_id}, side={side}, op={operation})"
        )
        assert len(key1) == 16, f"Key must be 16 hex chars, got {len(key1)}"
        assert all(c in "0123456789abcdef" for c in key1), (
            f"Key must be hex: {key1}"
        )

    @given(
        strategy=st.sampled_from(["BuyAboveThreshold", "MeanReversion"]),
        market_id=market_id_st,
        operation=st.sampled_from(["entry", "hedge"]),
    )
    @settings(max_examples=200, deadline=None)
    def test_different_side_produces_different_key(
        self, strategy, market_id, operation
    ):
        """∀ (strategy, market, operation): YES vs NO → different keys.

        Core R2.5.1 fix: execute_entry(YES) and execute_hedge(NO)
        must never collide for the same strategy+market+minute.
        """
        key_yes = RealTradingHandler._generate_idempotency_key(
            strategy_name=strategy,
            market_id=market_id,
            side="YES",
            operation=operation,
        )
        key_no = RealTradingHandler._generate_idempotency_key(
            strategy_name=strategy,
            market_id=market_id,
            side="NO",
            operation=operation,
        )

        assert key_yes != key_no, (
            f"R2.5.1: YES/NO collision: {key_yes} == {key_no} "
            f"(s={strategy}, m={market_id}, op={operation})"
        )

    @given(
        strategy=st.sampled_from(["BuyAboveThreshold", "MeanReversion"]),
        market_id=market_id_st,
        side=st.sampled_from(["YES", "NO"]),
    )
    @settings(max_examples=200, deadline=None)
    def test_different_operation_produces_different_key(
        self, strategy, market_id, side
    ):
        """∀ (strategy, market, side): all 4 operations → unique keys.

        execute_entry(), execute_exit(), execute_hedge(),
        redeem_resolved_position() must never collide.
        """
        keys = {}
        for operation in ["entry", "exit", "hedge", "redeem"]:
            keys[operation] = RealTradingHandler._generate_idempotency_key(
                strategy_name=strategy,
                market_id=market_id,
                side=side,
                operation=operation,
            )

        unique_keys = set(keys.values())
        assert len(unique_keys) == 4, (
            f"R2.5.1: Operations collision: {keys} "
            f"(s={strategy}, m={market_id}, side={side})"
        )

    @given(
        strategy=st.sampled_from(["BuyAboveThreshold", "MeanReversion"]),
        market_id=market_id_st,
        side=side_st,
        operation=operation_st,
    )
    @settings(max_examples=200, deadline=None)
    def test_key_hex_format_and_formula(
        self, strategy, market_id, side, operation
    ):
        """∀ inputs: key is 16-char SHA256 hex matching documented formula."""
        key = RealTradingHandler._generate_idempotency_key(
            strategy_name=strategy,
            market_id=market_id,
            side=side,
            operation=operation,
        )

        # Verify key format
        assert len(key) == 16, f"Key must be 16 chars, got {len(key)}: '{key}'"
        assert all(c in "0123456789abcdef" for c in key), (
            f"Non-hex chars in key: '{key}'"
        )

        # Verify formula: SHA256(strategy + market + side + operation + minute)[:16]
        now = datetime.now(timezone.utc)
        minute_bucket = now.strftime("%Y%m%d%H%M")
        raw = f"{strategy}{market_id}{side}{operation}{minute_bucket}"
        expected = hashlib.sha256(raw.encode()).hexdigest()[:16]

        assert key == expected, (
            f"R2.5.1: Formula mismatch: {key} != {expected} (raw='{raw}')"
        )
