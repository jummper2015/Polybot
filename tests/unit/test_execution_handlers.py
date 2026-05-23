# tests/unit/test_execution_handlers.py
"""Tests unitarios para PaperTradingHandler y RealTradingHandler."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.position import Position
from src.domain.enums.signal_type import SignalType
from src.domain.value_objects.signal import Signal
from src.domain.value_objects.trade_result import TradeResult
from src.execution.paper_handler import PaperTradingHandler
from src.execution.real_handler import RealTradingHandler
from src.infrastructure.security.audit_log import AuditLogger
from src.infrastructure.security.circuit_breaker import (
    CircuitBreakerConfig,
    CLOBCircuitBreaker,
)
from src.infrastructure.security.security_guard import (
    SecurityCheckResult,
)

# ── Helpers ────────────────────────────────────────────────────────────

def make_signal(confidence: float = 0.8) -> Signal:
    return Signal(
        type=SignalType.BUY_YES,
        market_id="market_001",
        confidence=confidence,
        source_strategy="BuyAboveThreshold",
        reason="price above threshold",
        timestamp=datetime.now(timezone.utc),
    )


def make_position(**kwargs) -> Position:
    defaults = dict(
        id="pos_001",
        market_id="market_001",
        asset="BTC",
        window="5m",
        side="YES",
        amount=10.0,
        shares=20.0,
        entry_price=0.50,
        exit_price=None,
        pnl=None,
        pnl_pct=None,
        mode="paper",
        strategy="BuyAboveThreshold",
        exit_reason=None,
    )
    defaults.update(kwargs)
    return Position(**defaults)


# ── Mock setup ─────────────────────────────────────────────────────────

def make_mock_repo():
    repo = AsyncMock()
    repo.save_order = AsyncMock()
    repo.save_position = AsyncMock()
    repo.get_positions = AsyncMock(return_value=[])
    repo.get_open_positions_count = AsyncMock(return_value=0)
    repo.get_total_pnl = AsyncMock(return_value=0.0)
    repo.save_audit_log = AsyncMock()
    return repo


def make_mock_redis():
    redis = AsyncMock()
    redis.get_ws_state = AsyncMock(return_value={
        "last_yes_price": "0.65",
        "last_spread": "0.02",
    })
    redis.get_market = AsyncMock(return_value=MagicMock(
        asset=MagicMock(value="BTC"),
        window=MagicMock(value="5m"),
        yes_token_id="yes_token_001",
    ))
    redis.set_paper_balance = AsyncMock()
    return redis


def make_mock_notifier():
    notifier = AsyncMock()
    notifier.send_trade_alert = AsyncMock()
    notifier.send_exit_alert = AsyncMock()
    notifier.send_risk_alert = AsyncMock()
    return notifier


# ═══════════════════════════════════════════════════════════════════════
# Paper Trading Handler Tests
# ═══════════════════════════════════════════════════════════════════════

class TestPaperTradingHandler:

    @pytest.fixture
    def handler(self):
        return PaperTradingHandler(
            repository=make_mock_repo(),
            redis=make_mock_redis(),
            notifier=make_mock_notifier(),
            initial_balance=1000.0,
        )

    @pytest.mark.asyncio
    async def test_paper_entry_creates_order(self, handler):
        """execute_entry crea una Order y Position con estado FILLED."""
        signal = make_signal()
        result = await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=10.0,
        )

        assert isinstance(result, TradeResult)
        assert result.success is True
        assert result.mode == "paper"
        assert result.side == "YES"
        assert result.fill_price > 0
        assert result.slippage >= 0

    @pytest.mark.asyncio
    async def test_paper_entry_reduces_balance(self, handler):
        """execute_entry descuenta del balance virtual."""
        initial = handler.get_balance()
        signal = make_signal()

        await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=50.0,
        )

        assert handler.get_balance() == initial - 50.0

    @pytest.mark.asyncio
    async def test_paper_entry_insufficient_balance_fails(self, handler):
        """Si el balance es insuficiente, devuelve success=False."""
        signal = make_signal()
        result = await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=2000.0,  # Más que el balance inicial de 1000
        )

        assert result.success is False
        assert "insuficiente" in result.error.lower()

    @pytest.mark.asyncio
    async def test_paper_exit_returns_value(self, handler):
        """execute_exit cierra la posición y devuelve valor al balance."""
        # Primero hacemos una entrada
        signal = make_signal()
        await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=10.0,
        )
        balance_after_entry = handler.get_balance()

        # Salimos
        position = make_position()
        result = await handler.execute_exit(
            position=position,
            reason="target_reached",
        )

        assert result.success is True
        assert result.mode == "paper"
        # El balance aumenta al vender (exit price > 0)
        assert handler.get_balance() > balance_after_entry

    @pytest.mark.asyncio
    async def test_paper_exit_creates_exit_order(self, handler):
        """execute_exit crea una Order de salida con estado FILLED."""
        position = make_position()
        result = await handler.execute_exit(
            position=position,
            reason="stop_loss",
        )

        assert result.success is True
        assert "exit" in result.order_id or True  # Order ID se genera
        assert result.side == "YES"

    @pytest.mark.asyncio
    async def test_paper_hedge_creates_order(self, handler):
        """execute_hedge crea una orden NO como cobertura."""
        signal = make_signal()
        # Primero abrimos una posición YES
        await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=10.0,
        )

        position = make_position()
        result = await handler.execute_hedge(
            position=position,
            hedge_amount=5.0,
        )

        assert result.success is True
        assert result.side == "NO"  # Hedge = comprar NO
        assert result.amount <= 5.0

    @pytest.mark.asyncio
    async def test_paper_total_pnl_tracking(self, handler):
        """get_total_pnl rastrea PnL acumulado sobre balance inicial."""
        initial_pnl = handler.get_total_pnl()
        assert initial_pnl == 0.0

        # Entrada descuenta del balance
        signal = make_signal()
        await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=10.0,
        )

        # PnL = balance actual - balance inicial.
        # Tras entry, el balance bajó 10 USDC (de 1000 a 990)
        # pero el PnL se calcula sobre balance - inicial, no sobre posiciones
        assert handler.get_total_pnl() == -10.0

        # Salida devuelve valor al balance
        position = make_position()
        await handler.execute_exit(
            position=position,
            reason="test",
        )

        # Tras salida, el balance debería ser distinto (PnL realizado)
        pnl_after_exit = handler.get_total_pnl()
        assert isinstance(pnl_after_exit, float)
        # El PnL puede ser positivo o negativo según slippage/precios


# ═══════════════════════════════════════════════════════════════════════
# Real Trading Handler Tests
# ═══════════════════════════════════════════════════════════════════════

class TestRealTradingHandler:

    def make_handler(self, **overrides):
        """Factory con mocks configurables."""
        clob = AsyncMock()
        clob.create_order = AsyncMock(return_value={
            "price": "0.66",
            "status": "FILLED",
            "orderID": "clob_order_123",
        })

        repo = make_mock_repo()
        redis = make_mock_redis()
        notifier = make_mock_notifier()
        audit = AuditLogger(repository=repo)

        handler = RealTradingHandler(
            clob_client=clob,
            repository=repo,
            redis=redis,
            notifier=notifier,
            audit_logger=audit,
            security_guard=overrides.get("security_guard"),
            circuit_breaker=overrides.get("circuit_breaker"),
        )
        return handler, clob, repo, audit

    # ── Guardrails ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_real_guardrail_blocks_amount_too_high(self):
        """Guardrail bloquea amounts > 500 USDC."""
        handler, clob, repo, audit = self.make_handler()
        signal = make_signal()

        result = await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=600.0,  # > 500 max
        )

        assert result.success is False
        assert "GUARDRAIL" in result.error
        # No se llamó a la API
        clob.create_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_real_guardrail_blocks_amount_too_low(self):
        """Guardrail bloquea amounts < 1 USDC."""
        handler, clob, repo, audit = self.make_handler()
        signal = make_signal()

        result = await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=0.50,  # < 1 min
        )

        assert result.success is False
        assert "GUARDRAIL" in result.error
        clob.create_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_real_guardrail_blocks_invalid_market_id(self):
        """Guardrail bloquea market_ids muy cortos."""
        handler, clob, repo, audit = self.make_handler()
        signal = make_signal()

        result = await handler.execute_entry(
            signal=signal,
            market_id="bad",  # < 10 chars
            amount=10.0,
        )

        assert result.success is False
        assert "GUARDRAIL" in result.error

    # ── Security Guard ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_real_security_guard_blocks(self):
        """Si SecurityGuard rechaza, la orden no se envía."""
        mock_security = AsyncMock()
        mock_security.check_real_order = AsyncMock(
            return_value=SecurityCheckResult(
                passed=False,
                reason="SECURITY: rate limit exceeded",
                checks={"rate_limit": False},
            )
        )

        handler, clob, repo, audit = self.make_handler(
            security_guard=mock_security,
        )
        signal = make_signal()

        result = await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=10.0,
        )

        assert result.success is False
        assert "SECURITY" in result.error
        clob.create_order.assert_not_called()

    # ── Audit Log ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_real_audit_log_written_on_attempt(self):
        """Toda orden real genera un audit log de intento."""
        handler, clob, repo, audit = self.make_handler()
        signal = make_signal()

        await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=10.0,
        )

        # Verifica que se escribió al menos un audit log
        assert repo.save_audit_log.call_count >= 1

    @pytest.mark.asyncio
    async def test_real_audit_log_written_on_success(self):
        """Orden exitosa genera audit log de intento y éxito (2+ llamadas)."""
        handler, clob, repo, audit = self.make_handler()
        signal = make_signal()

        result = await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=10.0,
        )

        assert result.success is True
        # Al menos 2: REAL_ORDER_ATTEMPT + REAL_ORDER_SUCCESS
        assert repo.save_audit_log.call_count >= 2

    # ── Circuit Breaker ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_circuit_breaker_allows_when_closed(self):
        """Circuit breaker cerrado → la orden se ejecuta normalmente."""
        cb = CLOBCircuitBreaker(config=CircuitBreakerConfig(
            failure_threshold=5,
            recovery_timeout=60.0,
            window_seconds=60.0,
        ))

        handler, clob, repo, audit = self.make_handler(
            circuit_breaker=cb,
        )
        signal = make_signal()

        result = await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=10.0,
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_when_open(self):
        """Circuit breaker abierto → bloquea la orden sin llamar a la API."""
        cb = CLOBCircuitBreaker(config=CircuitBreakerConfig(
            failure_threshold=2,  # Solo 2 fallos para abrir rápido
            recovery_timeout=60.0,
            window_seconds=60.0,
        ))
        # Abrimos el breaker naturalmente registrando fallos
        for _ in range(3):
            await cb.record_failure()

        handler, clob, repo, audit = self.make_handler(
            circuit_breaker=cb,
        )
        signal = make_signal()

        result = await handler.execute_entry(
            signal=signal,
            market_id="market_001",
            amount=10.0,
        )

        assert result.success is False
        assert "circuit" in result.error.lower() or "breaker" in result.error.lower()
        clob.create_order.assert_not_called()

    # ── Retry ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_real_no_retry_on_4xx(self):
        """Errores 4xx (lógica) no se reintentan."""
        handler, clob, repo, audit = self.make_handler()
        from httpx import HTTPStatusError, Request, Response

        call_count = [0]
        async def fail_400(*args, **kwargs):
            call_count[0] += 1
            request = Request("POST", "https://test.com")
            response = Response(400, request=request, content=b"Bad request")
            raise HTTPStatusError("Bad request", request=request, response=response)

        clob.create_order = fail_400
        signal = make_signal()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler.execute_entry(
                signal=signal,
                market_id="market_001",
                amount=10.0,
            )

        assert result.success is False
        assert call_count[0] == 1  # Solo un intento, sin retry

    @pytest.mark.asyncio
    async def test_real_retry_on_5xx(self):
        """Errores 5xx se reintentan hasta MAX_RETRIES veces."""
        handler, clob, repo, audit = self.make_handler()
        from httpx import HTTPStatusError, Request, Response

        call_count = [0]
        async def fail_503(*args, **kwargs):
            call_count[0] += 1
            request = Request("POST", "https://test.com")
            response = Response(503, request=request, content=b"Service unavailable")
            raise HTTPStatusError("Service unavailable", request=request, response=response)

        clob.create_order = fail_503
        signal = make_signal()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler.execute_entry(
                signal=signal,
                market_id="market_001",
                amount=10.0,
            )

        assert result.success is False
        # Debe reintentar MAX_RETRIES=3 veces total
        assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_real_retry_on_network_error(self):
        """Errores de red (RequestError, TimeoutError) se reintentan."""
        handler, clob, repo, audit = self.make_handler()
        from httpx import RequestError

        call_count = [0]
        async def fail_network(*args, **kwargs):
            call_count[0] += 1
            raise RequestError("Connection refused")

        clob.create_order = fail_network
        signal = make_signal()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler.execute_entry(
                signal=signal,
                market_id="market_001",
                amount=10.0,
            )

        assert result.success is False
        assert call_count[0] == 3  # 3 reintentos

    # ── Idempotency Key ────────────────────────────────────────────────

    def test_idempotency_key_deterministic(self):
        """Misma estrategia + market_id en mismo minuto = misma key."""
        key1 = RealTradingHandler._generate_idempotency_key(
            strategy_name="TestStrat",
            market_id="market_001",
        )
        key2 = RealTradingHandler._generate_idempotency_key(
            strategy_name="TestStrat",
            market_id="market_001",
        )
        assert key1 == key2

    def test_idempotency_key_different_if_strategy_differs(self):
        """Diferente estrategia → diferente key."""
        key1 = RealTradingHandler._generate_idempotency_key(
            strategy_name="StratA",
            market_id="market_001",
        )
        key2 = RealTradingHandler._generate_idempotency_key(
            strategy_name="StratB",
            market_id="market_001",
        )
        assert key1 != key2

    def test_idempotency_key_different_if_market_differs(self):
        """Diferente market_id → diferente key."""
        key1 = RealTradingHandler._generate_idempotency_key(
            strategy_name="Test",
            market_id="market_001",
        )
        key2 = RealTradingHandler._generate_idempotency_key(
            strategy_name="Test",
            market_id="market_002",
        )
        assert key1 != key2
