"""tests/property/test_paper_redeem_invariants.py

Property-based tests (Hypothesis) for PaperTradingHandler.redeem_resolved_position
payout invariants — R3.x paper-mode redeem simulation.

Invariantes bajo prueba:

  win branch (side == winning_outcome):
    W1 \u2014 payout_usd == position.shares (full $1 per winning share)
    W2 \u2014 fill_price == 1.0 (CTF settlement price for winning token)
    W3 \u2014 position.pnl == payout \u2212 entry_amount  (gain on win)
    W4 \u2014 balance_post == balance_pre + payout  (paper balance updated)
    W5 \u2014 position.is_open == False  (closure)
    W6 \u2014 position.closed_at is not None  (closure timestamp)
    W7 \u2014 position.exit_reason startswith "redeemed:"  (audit trace)

  loss branch (side != winning_outcome):
    L1 \u2014 payout_usd == 0.0  (losing token settles at $0)
    L2 \u2014 fill_price == 0.0  (CTF settlement for losing token)
    L3 \u2014 position.pnl == \u2212entry_amount  (lost full capital)
    L4 \u2014 balance_post == balance_pre  (no accretion on loss)
    L5 \u2014 position.is_open == False  (still closed)

  bounds:
    I1 \u2014 invalid winning_outcome \u2192 TradeResult.success=False
    I2 \u2014 payout \u2208 [0, shares] for all inputs  (no overpayment, no double-claim)
    I3 \u2014 idempotent: 2\u00aa redeem no modifica balance ni position state

Refs RECORRIDO_ACTUAL.md \u00a7Redeem Simulation (paper).
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.domain.entities.position import Position
from src.domain.enums.market_status import MarketStatus
from src.execution.paper_handler import PaperTradingHandler

# \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# Strategies (Hypothesis)
# \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

side_st = st.sampled_from(["YES", "NO", "yes", "no"])
winning_st = st.sampled_from(["YES", "NO", "yes", "no", "Yes", "No"])
invalid_st = st.text(
    min_size=1,
    max_size=10,
    alphabet=st.characters(
        whitelist_categories=("L", "Lu"),
    ),
).filter(lambda s: s.upper().strip() not in ("YES", "NO"))

# shares: 0.001 (m\u00ednimo) a 1000 (max cap) \u2014 rango realista paper trading.
shares_st = st.floats(
    min_value=0.001,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)
# entry_price: 0.01 a 0.99 (rango binario).
entry_price_st = st.floats(
    min_value=0.01,
    max_value=0.99,
    allow_nan=False,
    allow_infinity=False,
)
# initial paper balance.
balance_st = st.floats(
    min_value=1.0,
    max_value=100000.0,
    allow_nan=False,
    allow_infinity=False,
)


# \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# Helpers
# \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


def make_position(
    side: str,
    shares: float,
    entry_price: float = 0.5,
    market_id: str = "test_market",
) -> Position:
    """Crea una Position abierta para property tests."""
    return Position(
        id=str(uuid.uuid4()),
        market_id=market_id,
        asset="BTC",
        window="5m",
        side=side.upper(),
        amount=round(shares * entry_price, 4),
        shares=shares,
        entry_price=entry_price,
        exit_price=None,
        pnl=None,
        pnl_pct=None,
        mode="paper",
        strategy="Test",
        exit_reason=None,
    )


def make_paper_handler(
    *,
    initial_balance: float = 1000.0,
    market_status: MarketStatus = MarketStatus.RESOLVED,
    open_position: Position | None = None,
) -> PaperTradingHandler:
    """
    Construye un PaperTradingHandler con todas las deps mocks.
    AsyncMock para todos los m\u00e9todos awaitable (evita TypeError en await).
    """
    repo = MagicMock()
    redis = MagicMock()

    # \u2014\u2014 Redis mock \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
    mock_market = MagicMock()
    mock_market.status = market_status
    redis.get_market = AsyncMock(return_value=mock_market)
    redis.set_paper_balance = AsyncMock()  # CRITICAL: set_paper_balance es awaitable

    # \u2014\u2014 Repo mock \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
    repo.get_positions = AsyncMock(return_value=[open_position] if open_position else [])
    repo.save_position = AsyncMock()  # CRITICAL: save_position es awaitable
    repo.save_order = AsyncMock()

    handler = PaperTradingHandler(
        repository=repo,
        redis=redis,
        notifier=None,
        initial_balance=initial_balance,
    )
    return handler


def asyncio_run(coro):
    """Helper: ejecuta una coroutine en un nuevo event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# WIN BRANCH \u2014 side == winning_outcome \u2192 payout = shares \u00d7 1.0
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550


class TestRedeemWinBranchInvariants:
    """Side == winning_outcome \u2192 redeem completo: payout = $1 per share."""

    @given(
        side=side_st,
        winning=winning_st,
        shares=shares_st,
        entry_price=entry_price_st,
        pre_balance=balance_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_w1_w7_win_branch(self, side, winning, shares, entry_price, pre_balance):
        """Branch WIN: payout = shares \u00d7 1.0, position cerrada, balance++."""
        side_norm = side.upper().strip()
        win_norm = winning.upper().strip()
        if side_norm != win_norm:
            return  # branch WIN \u2192 salir; branch LOSS cubierto en otra clase

        position = make_position(
            side=side,
            shares=shares,
            entry_price=entry_price,
        )
        handler = make_paper_handler(
            initial_balance=pre_balance,
            open_position=position,
        )

        result = asyncio_run(handler.redeem_resolved_position("test_market", winning))

        # W1: payout = shares \u00d7 1.0
        assert result.success, f"Win should succeed, got {result.error}"
        expected_payout = round(shares * 1.0, 4)
        assert result.amount == pytest.approx(
            expected_payout, abs=1e-4
        ), f"W1 failed: payout={result.amount} != shares\u00d71.0={expected_payout}"

        # W2: fill_price = 1.0
        assert result.fill_price == 1.0, f"W2 failed: fill_price={result.fill_price} != 1.0"

        # W3: pnl = payout \u2212 entry_amount (ganancia cuando entry < 1.0)
        expected_pnl = round(shares - position.amount, 4)
        assert result.pnl is not None
        assert abs(result.pnl - expected_pnl) < 0.01, (
            f"W3 failed: pnl={result.pnl} != ~{expected_pnl} "
            f"(shares={shares}, amount={position.amount})"
        )

        # W4: balance_post = balance_pre + payout
        assert handler._balance == pytest.approx(pre_balance + expected_payout, abs=1e-3), (
            f"W4 failed: post_balance={handler._balance} != "
            f"pre={pre_balance} + payout={expected_payout}"
        )

        # W5: position cerrada
        assert position.is_open is False, "W5 failed: position should be closed"
        # W6: closed_at set
        assert position.closed_at is not None, "W6 failed: closed_at not set"
        # W7: exit_reason starts with "redeemed:"
        assert position.exit_reason is not None
        assert position.exit_reason.startswith(
            "redeemed:"
        ), f"W7 failed: exit_reason='{position.exit_reason}'"


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# LOSS BRANCH \u2014 side != winning_outcome \u2192 payout = 0
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550


class TestRedeemLossBranchInvariants:
    """Side != winning_outcome \u2192 payout = 0 (CTF loses settle at $0)."""

    @given(
        side=side_st,
        winning=winning_st,
        shares=shares_st,
        entry_price=entry_price_st,
        pre_balance=balance_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_l1_l5_loss_branch(self, side, winning, shares, entry_price, pre_balance):
        """Branch LOSS: payout = 0, balance unchanged, position cerrada."""
        side_norm = side.upper().strip()
        win_norm = winning.upper().strip()
        if side_norm == win_norm:
            return  # branch WIN cubierto arriba

        position = make_position(
            side=side,
            shares=shares,
            entry_price=entry_price,
        )
        handler = make_paper_handler(
            initial_balance=pre_balance,
            open_position=position,
        )

        result = asyncio_run(handler.redeem_resolved_position("test_market", winning))

        # L1: payout = 0
        assert result.success, f"Loss should still succeed, got {result.error}"
        assert result.amount == 0.0, f"L1 failed: payout={result.amount} != 0"
        # L2: fill_price = 0
        assert result.fill_price == 0.0, f"L2 failed: fill_price={result.fill_price} != 0.0"
        # L3: pnl = 0 \u2212 entry_amount = \u2212amount
        expected_pnl = round(0.0 - position.amount, 4)
        assert result.pnl is not None
        assert (
            abs(result.pnl - expected_pnl) < 0.01
        ), f"L3 failed: pnl={result.pnl} != ~{expected_pnl} (-amount)"
        # L4: balance sin cambios
        assert handler._balance == pytest.approx(
            pre_balance, abs=1e-3
        ), f"L4 failed: post_balance={handler._balance} != pre={pre_balance}"
        # L5: position cerrada
        assert position.is_open is False, "L5 failed: position should be closed"


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# OVERPAYMENT GUARD \u2014 payout nunca excede shares \u00d7 1.0
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550


class TestRedeemPayoutBounded:
    """
    Property defensivo: payout \u2208 [0, shares] para cualquier input.

    Si hipot\u00e9ticamente el handler bug introdujera un multiplicador
    mayor a 1.0 o duplicara el redeem, esta property lo detectar\u00eda.
    """

    @given(
        shares=shares_st,
        position_side=side_st,
        winning=winning_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_i2_payout_bounded_by_shares(self, shares, position_side, winning):
        """I2: 0 \u2264 payout \u2264 shares (no overpayment, no double-claim)."""
        position = make_position(
            side=position_side,
            shares=shares,
            entry_price=0.5,
        )
        handler = make_paper_handler(open_position=position)
        result = asyncio_run(handler.redeem_resolved_position("test_market", winning))
        assert result.success, f"Bounded test assumes success, got {result.error}"
        assert (
            0.0 <= result.amount <= shares + 1e-4
        ), f"I2 failed: payout={result.amount} \u2209 [0, {shares}]"


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# IDEMPOTENCIA \u2014 2\u00aa redeem no aplica side effects
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550


class TestRedeemIdempotency:
    """
    2\u00aa redeem sobre posici\u00f3n ya cerrada \u2192 success=False + no modifica side effects.

    Defense-in-depth: si por alguna raz\u00f3n el handler bug permitiera
    re-redimir, el balance se duplicar\u00eda y el PnL se publicar\u00eda dos
    veces. Este test detecta ese escenario.
    """

    @pytest.mark.asyncio
    async def test_double_redeem_no_double_claim(self):
        position = make_position(side="YES", shares=10.0, entry_price=0.5)
        handler = make_paper_handler(
            initial_balance=1000.0,
            open_position=position,
        )

        # 1\u00aa redeem: payout \u00fanico (YES = winning_outcome).
        first = await handler.redeem_resolved_position("test_market", "YES")
        assert first.success
        assert first.amount == 10.0
        balance_after_first = handler._balance
        assert balance_after_first == 1010.0

        # 2\u00aa redeem: la posici\u00f3n ya est\u00e1 cerrada; el mock get_positions
        # devolver\u00e1 []. Esto es lo que pasar\u00eda en prod tras el primer
        # save_position (open_only=True deja de verla).
        handler._repo.get_positions = AsyncMock(return_value=[])
        second = await handler.redeem_resolved_position("test_market", "YES")

        assert not second.success, "2\u00aa redeem debe ser no-op"
        assert second.error == "no_open_position"
        assert handler._balance == balance_after_first, (
            f"Idempotencia rota: balance {handler._balance} != " f"{balance_after_first}"
        )


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# INPUT VALIDATION \u2014 invalid winning_outcome \u2192 success=False
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550


class TestRedeemInputValidation:
    """winning_outcome inv\u00e1lido \u2192 success=False con error explicativo."""

    @given(invalid_outcome=invalid_st)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_i1_invalid_outcome_fails_cleanly(self, invalid_outcome):
        position = make_position(side="YES", shares=10.0, entry_price=0.5)
        handler = make_paper_handler(open_position=position)
        result = asyncio_run(handler.redeem_resolved_position("test_market", invalid_outcome))

        assert not result.success, "Invalid outcome debe ser rejected"
        assert result.error is not None
        assert (
            "winning_outcome" in result.error.lower()
        ), f"I1 failed: error='{result.error}' no menciona winning_outcome"
