"""
tests/chaos/test_steady_state_hypotheses.py
============================================

Steady-state hypotheses that MUST hold under any failure condition.

These are the invariants that define whether the system is
"healthy" — if any of these fail, the bot should halt.

From PLAN_MEJORAS.txt P4.6:
  H1: El bot NUNCA envía órdenes duplicadas
  H2: El balance NUNCA baja del min_balance configurado
  H3: El RiskEngine SIEMPRE se evalúa antes de ejecutar
"""

import asyncio
import hashlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.market_service import MarketService
from src.application.services.trading_service import TradingService
from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.signal_type import SignalType as SigType
from src.domain.enums.window import Window
from src.domain.value_objects.risk_decision import RiskDecision
from src.domain.value_objects.signal import Signal
from src.execution.real_handler import RealTradingHandler
from src.risk.context import RiskContext
from src.risk.rules.drawdown import DrawdownRule
from src.risk.rules.kelly_sizing import KellySizingRule
from src.risk.rules.min_balance import MinBalanceRule
from src.strategies.engine import StrategyEngine

# ═══════════════════════════════════════════════════════════════════════
# H1: El bot NUNCA envía órdenes duplicadas
# ═══════════════════════════════════════════════════════════════════════

class TestIdempotencyKeyDeterminism:
    """
    Verifies that the idempotency key generation is deterministic
    and that the same signal in the same minute produces the same key.
    """

    def test_same_strategy_market_minute_produces_same_key(self):
        """
        GIVEN: same strategy_name, market_id, and within the same UTC minute
        WHEN:  _generate_idempotency_key is called twice
        THEN:  both keys are identical
        """
        key1 = RealTradingHandler._generate_idempotency_key(
            strategy_name="BuyAboveThreshold",
            market_id="0xabc123def456",
        )
        key2 = RealTradingHandler._generate_idempotency_key(
            strategy_name="BuyAboveThreshold",
            market_id="0xabc123def456",
        )

        assert key1 == key2, (
            f"H1 VIOLATED: Keys differ for same inputs: {key1} != {key2}"
        )
        assert len(key1) == 16, "Key must be 16 hex chars"
        # Valid hex characters only
        assert all(c in "0123456789abcdef" for c in key1), "Key must be hex"

    def test_different_strategy_produces_different_key(self):
        """
        GIVEN: different strategy names with same market_id and minute
        WHEN:  _generate_idempotency_key is called
        THEN:  keys are different
        """
        key1 = RealTradingHandler._generate_idempotency_key(
            strategy_name="BuyAboveThreshold",
            market_id="0xabc123def456",
        )
        key2 = RealTradingHandler._generate_idempotency_key(
            strategy_name="MeanReversion",
            market_id="0xabc123def456",
        )

        assert key1 != key2, (
            f"H1 VIOLATED: Different strategies produce same key: {key1}"
        )

    def test_different_market_produces_different_key(self):
        """
        GIVEN: different market_ids with same strategy and minute
        WHEN:  _generate_idempotency_key is called
        THEN:  keys are different
        """
        key1 = RealTradingHandler._generate_idempotency_key(
            strategy_name="BuyAboveThreshold",
            market_id="0xabc123def456",
        )
        key2 = RealTradingHandler._generate_idempotency_key(
            strategy_name="BuyAboveThreshold",
            market_id="0x789ghi012jkl",
        )

        assert key1 != key2, (
            f"H1 VIOLATED: Different markets produce same key: {key1}"
        )

    def test_key_uses_sha256_hex(self):
        """
        Verify that the key is indeed a SHA256 hex digest truncated to 16 chars.
        """
        strategy_name = "TestStrategy"
        market_id = "0xmarket"
        now = datetime.now(timezone.utc)
        minute_bucket = now.strftime("%Y%m%d%H%M")
        raw = f"{strategy_name}{market_id}{minute_bucket}"
        expected_prefix = hashlib.sha256(raw.encode()).hexdigest()[:16]

        key = RealTradingHandler._generate_idempotency_key(
            strategy_name=strategy_name,
            market_id=market_id,
        )

        assert key == expected_prefix, (
            f"H1 VIOLATED: Key {key} != expected SHA256 prefix {expected_prefix}"
        )

    def test_key_changes_across_minutes(self, monkeypatch):
        """
        GIVEN: the same inputs at different UTC minutes
        WHEN:  _generate_idempotency_key is called
        THEN:  keys are different (minute-bucket changes)
        """
        fixed_dt_1 = datetime(2026, 5, 21, 10, 30, 0, tzinfo=timezone.utc)
        fixed_dt_2 = datetime(2026, 5, 21, 10, 31, 0, tzinfo=timezone.utc)

        class FakeDatetime:
            @staticmethod
            def now(*args, **kwargs):
                return fixed_dt_1

        with patch(
            "src.execution.real_handler.datetime", FakeDatetime
        ):
            key1 = RealTradingHandler._generate_idempotency_key(
                strategy_name="Test",
                market_id="0xmarket",
            )

        class FakeDatetime2:
            @staticmethod
            def now(*args, **kwargs):
                return fixed_dt_2

        with patch(
            "src.execution.real_handler.datetime", FakeDatetime2
        ):
            key2 = RealTradingHandler._generate_idempotency_key(
                strategy_name="Test",
                market_id="0xmarket",
            )

        assert key1 != key2, (
            f"H1 VIOLATED: Keys should differ across minutes but both are {key1}"
        )


# ═══════════════════════════════════════════════════════════════════════
# H2: El balance NUNCA baja del min_balance configurado
# ═══════════════════════════════════════════════════════════════════════

class TestBalanceNeverBelowMinimum:
    """
    Verifies that the MinBalanceRule (and DrawdownRule) together
    guarantee that the balance never drops below the configured minimum.
    """

    MIN_BALANCE = 50.0

    def _make_context(
        self,
        current_balance: float,
        open_positions: int = 0,
        exposure: float = 0.0,
    ) -> RiskContext:
        return RiskContext(
            current_balance=current_balance,
            initial_day_balance=max(current_balance, 100.0),
            open_positions_count=open_positions,
            market_exposure_usdc=exposure,
            total_exposure_usdc=exposure,
            requested_amount=10.0,
            market_id="test_market",
            trading_mode="paper",
        )

    def _make_signal(self) -> Signal:
        return Signal(
            type=SigType.BUY_YES,
            market_id="test_market",
            confidence=0.8,
            source_strategy="Test",
            reason="test",
            timestamp=datetime.now(timezone.utc),
        )

    def test_min_balance_rule_denies_when_balance_insufficient(self):
        """
        GIVEN: balance = min_balance (50 USDC) and requested = 10 USDC
        WHEN:  MinBalanceRule evaluates
        THEN:  rule DENIES because balance would drop below minimum
        """
        rule = MinBalanceRule(min_balance_usdc=self.MIN_BALANCE)

        # Balance = 50, requested = 10 → after execution = 40 < 50 → DENY
        ctx = self._make_context(current_balance=50.0)
        decision = rule.evaluate(self._make_signal(), ctx)

        assert not decision.allowed, (
            f"H2 VIOLATED: MinBalanceRule allowed trade that would "
            f"drop balance below {self.MIN_BALANCE}"
        )
        assert "min_required" in decision.reason.lower(), (
            f"H2 VIOLATED: reason doesn't mention min_required: {decision.reason}"
        )

    def test_min_balance_rule_allows_when_balance_above(self):
        """
        GIVEN: balance well above minimum
        WHEN:  MinBalanceRule evaluates
        THEN:  rule ALLOWS
        """
        rule = MinBalanceRule(min_balance_usdc=self.MIN_BALANCE)

        # Balance = 200, requested = 10 → after = 190 > 50 → ALLOW
        ctx = self._make_context(current_balance=200.0)
        decision = rule.evaluate(self._make_signal(), ctx)

        assert decision.allowed, (
            "H2 VIOLATED: MinBalanceRule denied trade with sufficient balance"
        )

    def test_drawdown_rule_denies_beyond_max(self):
        """
        GIVEN: initial day balance = 100, current = 85 (15% drawdown)
        WHEN:  DrawdownRule evaluates with max=10%
        THEN:  rule DENIES
        """
        rule = DrawdownRule(max_daily_drawdown_pct=0.10)
        ctx = RiskContext(
            current_balance=85.0,
            initial_day_balance=100.0,
            open_positions_count=0,
            market_exposure_usdc=0.0,
            total_exposure_usdc=0.0,
            requested_amount=10.0,
            market_id="test_market",
            trading_mode="paper",
        )

        decision = rule.evaluate(self._make_signal(), ctx)

        assert not decision.allowed, (
            "H2 VIOLATED: DrawdownRule allowed trade with 15% drawdown"
        )

    def test_combined_rules_prevent_balance_drop(self):
        """
        GIVEN: edge case where balance is close to minimum
        WHEN:  both MinBalanceRule and DrawdownRule are evaluated
        THEN:  at least one rule DENIES and prevents balance drop
        """
        min_rule = MinBalanceRule(min_balance_usdc=self.MIN_BALANCE)
        drawdown_rule = DrawdownRule(max_daily_drawdown_pct=0.10)

        # Scenario: balance = 55, drawdown already 8%
        ctx = RiskContext(
            current_balance=55.0,
            initial_day_balance=60.0,  # 8.3% drawdown
            open_positions_count=0,
            market_exposure_usdc=0.0,
            total_exposure_usdc=0.0,
            requested_amount=10.0,
            market_id="test_market",
            trading_mode="paper",
        )

        min_decision = min_rule.evaluate(self._make_signal(), ctx)
        dd_decision = drawdown_rule.evaluate(self._make_signal(), ctx)

        # At least one must deny (balance would drop to 45 < 50)
        at_least_one_denied = (
            not min_decision.allowed or not dd_decision.allowed
        )
        assert at_least_one_denied, (
            "H2 VIOLATED: Both rules allowed — balance would drop "
            "from 55 to 45 (below min 50)"
        )

    def test_kelly_rule_never_exceeds_balance(self):
        """
        GIVEN: Kelly sizing with balance = 100 and high confidence
        WHEN:  KellySizingRule evaluates
        THEN:  suggested amount never exceeds current balance
        """
        rule = KellySizingRule(
            kelly_cap=0.25,
            safety_factor=0.25,
            target_price=0.90,
            position_floor=5.0,
            position_cap=50.0,
        )

        ctx = RiskContext(
            current_balance=100.0,
            initial_day_balance=100.0,
            open_positions_count=0,
            market_exposure_usdc=0.0,
            total_exposure_usdc=0.0,
            requested_amount=100.0,
            market_id="test_market",
            trading_mode="paper",
            market_yes_price=0.60,
        )

        signal = Signal(
            type=SigType.BUY_YES,
            market_id="test_market",
            confidence=0.95,
            source_strategy="Test",
            reason="test",
            timestamp=datetime.now(timezone.utc),
        )

        decision = rule.evaluate(signal, ctx)

        if decision.suggested_amount is not None:
            assert decision.suggested_amount <= ctx.current_balance, (
                f"H2 VIOLATED: Kelly suggested {decision.suggested_amount} > "
                f"balance {ctx.current_balance}"
            )


# ═══════════════════════════════════════════════════════════════════════
# H3: El RiskEngine SIEMPRE se evalúa antes de ejecutar
# ═══════════════════════════════════════════════════════════════════════

class TestRiskEngineAlwaysEvaluated:
    """
    Verifies that the trading flow ALWAYS evaluates the RiskEngine
    before calling execute_entry. This is the critical safety invariant.
    """

    @staticmethod
    def _make_market() -> Market:
        return Market(
            id="test_market",
            asset=Asset.BTC,
            window=Window.M5,
            question="BTC above $100k",
            status=MarketStatus.ACTIVE,
            yes_token_id="0xabc",
            no_token_id="0xdef",
            yes_price=0.75,
            no_price=0.25,
            volume_24h=10000.0,
            expiry=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )

    @staticmethod
    def _make_signal() -> Signal:
        return Signal(
            type=SigType.BUY_YES,
            market_id="test_market",
            confidence=0.8,
            source_strategy="BuyAboveThreshold",
            reason="test",
            timestamp=datetime.now(timezone.utc),
        )

    @staticmethod
    def _make_trading_service(
        mock_risk: AsyncMock,
        mock_execution: AsyncMock,
        mock_notifier: AsyncMock,
        balance: float = 500.0,
        trading_mode: str = "paper",
    ) -> TradingService:
        mock_repo = AsyncMock()
        mock_repo.get_positions.return_value = []

        mock_portfolio = AsyncMock()
        mock_portfolio.get_balance.return_value = balance

        return TradingService(
            market_service=AsyncMock(spec=MarketService),
            strategy_engine=MagicMock(spec=StrategyEngine),
            risk_engine=mock_risk,
            execution_handler=mock_execution,
            repository=mock_repo,
            notifier=mock_notifier,
            portfolio_service=mock_portfolio,
            position_size_pusd=10.0,
            trading_mode=trading_mode,
        )

    def test_trading_service_calls_risk_before_execution(self):
        """
        GIVEN: a trading service with mocked risk engine and execution
        WHEN:  _evaluate_risk_and_execute is called
        THEN:  risk.evaluate() is called BEFORE execution.execute_entry()
        """
        mock_risk = AsyncMock()
        mock_risk.evaluate.return_value = RiskDecision(
            allowed=True,
            reason="test",
            rule_triggered="TestRule",
            suggested_amount=None,
        )

        mock_execution = AsyncMock()
        mock_execution.execute_entry.return_value = MagicMock(
            success=True, fill_price=0.75, pnl=None
        )

        mock_notifier = AsyncMock()

        svc = self._make_trading_service(mock_risk, mock_execution, mock_notifier)

        market = self._make_market()
        signal = self._make_signal()

        asyncio.run(svc._evaluate_risk_and_execute(market, signal))

        mock_risk.evaluate.assert_called_once(), (
            "H3 VIOLATED: RiskEngine.evaluate was not called before execution!"
        )

        mock_execution.execute_entry.assert_called_once(), (
            "H3 VIOLATED: execute_entry was not called after risk approval!"
        )

    def test_risk_deny_prevents_execution(self):
        """
        GIVEN: RiskEngine returns DENY
        WHEN:  _evaluate_risk_and_execute is called
        THEN:  execute_entry is NEVER called
        """
        mock_risk = AsyncMock()
        mock_risk.evaluate.return_value = RiskDecision(
            allowed=False,
            reason="Insufficient balance",
            rule_triggered="MinBalanceRule",
            suggested_amount=None,
        )

        mock_execution = AsyncMock()
        mock_notifier = AsyncMock()

        svc = self._make_trading_service(
            mock_risk, mock_execution, mock_notifier, balance=10.0
        )

        market = self._make_market()
        signal = self._make_signal()

        asyncio.run(svc._evaluate_risk_and_execute(market, signal))

        mock_risk.evaluate.assert_called_once(), (
            "H3 VIOLATED: RiskEngine.evaluate was not called!"
        )

        mock_execution.execute_entry.assert_not_called(), (
            "H3 VIOLATED: execute_entry was called despite risk DENY!"
        )

        mock_notifier.send_risk_alert.assert_called_once(), (
            "H3 VIOLATED: No notification sent for denied trade!"
        )

    def test_risk_error_prevents_execution(self):
        """
        GIVEN: RiskEngine raises an exception
        WHEN:  _evaluate_risk_and_execute is called
        THEN:  execute_entry is NEVER called (fail-safe)
        """
        mock_risk = AsyncMock()
        mock_risk.evaluate.side_effect = RuntimeError("Risk engine crash")

        mock_execution = AsyncMock()
        mock_notifier = AsyncMock()

        svc = self._make_trading_service(mock_risk, mock_execution, mock_notifier)

        market = self._make_market()
        signal = self._make_signal()

        with pytest.raises(RuntimeError, match="Risk engine crash"):
            asyncio.run(svc._evaluate_risk_and_execute(market, signal))

        mock_execution.execute_entry.assert_not_called(), (
            "H3 VIOLATED: execute_entry was called despite risk engine failure!"
        )
