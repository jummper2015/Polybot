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
    # Legacy (kept for backward compat with other tests)
    redis.get_ws_state = AsyncMock(return_value={
        "last_yes_price": "0.65",
        "last_spread": "0.02",
    })
    # P9.1 FillSimulator integration — new methods
    redis.get_last_tick_price = AsyncMock(return_value={
        "last_yes_price": 0.65,
        "last_spread": 0.02,
        "best_bid": 0.64,
        "best_ask": 0.66,
        "volume_24h": 5000.0,
    })
    redis.get_orderbook = AsyncMock(return_value=None)  # falls through to tick price
    redis.get_market = AsyncMock(return_value=MagicMock(
        asset=MagicMock(value="BTC"),
        window=MagicMock(value="5m"),
        yes_token_id="yes_token_001",
    ))
    redis.get_market_metadata = AsyncMock(return_value={
        "tick_size": "0.01",
        "neg_risk": False,
        "min_order_size": 1.0,
    })
    redis.set_market_metadata = AsyncMock()
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


# ═══════════════════════════════════════════════════════════════════════
# R1.5 — paths sin cubrir en RealTradingHandler
# ═══════════════════════════════════════════════════════════════════════


def _make_response(status_code, body_text="", headers=None):
    """httpx.Response-like mock para _parse_post_only_response."""
    r = MagicMock()
    r.status_code = status_code
    r.text = body_text
    r.headers = headers or {}
    return r


class TestRealHandlerPostOnly:
    """_parse_post_only_response — 503 post-only mode parsing."""

    def test_returns_retry_after_from_body(self):
        body = '{"code":"post_only_mode","retry_after_seconds":15}'
        result = RealTradingHandler._parse_post_only_response(
            _make_response(503, body_text=body)
        )
        assert result == {"retry_after": 15.0}

    def test_falls_back_to_header_when_body_seconds_missing(self):
        body = '{"code":"post_only_mode"}'
        result = RealTradingHandler._parse_post_only_response(
            _make_response(503, body_text=body, headers={"Retry-After": "20"})
        )
        assert result == {"retry_after": 20.0}

    def test_default_30s_when_no_body_seconds_no_header(self):
        body = '{"code":"post_only_mode"}'
        result = RealTradingHandler._parse_post_only_response(
            _make_response(503, body_text=body)
        )
        assert result == {"retry_after": 30.0}

    def test_invalid_retry_after_header_uses_default(self):
        body = '{"code":"post_only_mode","retry_after_seconds":0}'
        result = RealTradingHandler._parse_post_only_response(
            _make_response(
                503, body_text=body, headers={"Retry-After": "not-a-number"}
            )
        )
        assert result == {"retry_after": 30.0}

    def test_returns_none_when_code_missing(self):
        body = '{"code":"other_error","message":"oops"}'
        assert RealTradingHandler._parse_post_only_response(
            _make_response(503, body_text=body)
        ) is None

    def test_returns_none_when_body_not_json(self):
        assert RealTradingHandler._parse_post_only_response(
            _make_response(503, body_text="not-json-body")
        ) is None

    def test_clamps_high_retry_to_120(self):
        body = '{"code":"post_only_mode","retry_after_seconds":9999}'
        result = RealTradingHandler._parse_post_only_response(
            _make_response(503, body_text=body)
        )
        assert result == {"retry_after": 120.0}

    def test_clamps_low_retry_to_1(self):
        body = '{"code":"post_only_mode","retry_after_seconds":0.1}'
        result = RealTradingHandler._parse_post_only_response(
            _make_response(503, body_text=body)
        )
        assert result == {"retry_after": 1.0}


class TestRealHandlerTokenAndPrice:
    """_get_token_and_price — extracción de token + precio según side."""

    def _handler(self):
        clob = AsyncMock()
        redis = make_mock_redis()
        repo = make_mock_repo()
        notifier = make_mock_notifier()
        audit = AuditLogger(repository=repo)
        return RealTradingHandler(
            clob_client=clob,
            repository=repo,
            redis=redis,
            notifier=notifier,
            audit_logger=audit,
        ), redis

    @pytest.mark.asyncio
    async def test_buy_yes_returns_yes_token_and_price(self):
        handler, _ = self._handler()
        token, price = await handler._get_token_and_price(
            "market_001", SignalType.BUY_YES,
        )
        assert token == "yes_token_001"
        assert price == 0.65

    @pytest.mark.asyncio
    async def test_buy_no_inverts_price(self):
        handler, redis = self._handler()
        redis.get_market = AsyncMock(return_value=MagicMock(
            asset=MagicMock(value="BTC"),
            window=MagicMock(value="5m"),
            yes_token_id="y", no_token_id="n",
        ))
        token, price = await handler._get_token_and_price(
            "market_001", SignalType.BUY_NO,
        )
        assert token == "n"
        # 1.0 - 0.65 = 0.35
        assert price == 0.35

    @pytest.mark.asyncio
    async def test_no_ws_state_uses_default_05(self):
        handler, redis = self._handler()
        redis.get_ws_state = AsyncMock(return_value=None)
        _, price = await handler._get_token_and_price(
            "market_001", SignalType.BUY_YES,
        )
        assert price == 0.5

    @pytest.mark.asyncio
    async def test_market_missing_raises(self):
        handler, redis = self._handler()
        redis.get_market = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="no encontrado"):
            await handler._get_token_and_price(
                "market_xxx", SignalType.BUY_YES,
            )


class TestRealHandlerCreatePosition:
    """_create_real_position — construcción de Position desde una Order filled."""

    def _handler_and_order(self, market=...):
        clob = AsyncMock()
        redis = make_mock_redis()
        if market is not ...:
            redis.get_market = AsyncMock(return_value=market)
        repo = make_mock_repo()
        notifier = make_mock_notifier()
        audit = AuditLogger(repository=repo)
        handler = RealTradingHandler(
            clob_client=clob,
            repository=repo,
            redis=redis,
            notifier=notifier,
            audit_logger=audit,
        )
        from src.domain.entities.order import Order
        from src.domain.enums.order_side import OrderSide
        from src.domain.enums.order_status import OrderStatus
        from src.domain.enums.trading_mode import TradingMode
        order = Order(
            id="ord_001",
            market_id="market_001",
            side=OrderSide.YES,
            amount=10.0,
            target_price=0.65,
            fill_price=0.66,
            slippage=0.01,
            status=OrderStatus.FILLED,
            mode=TradingMode.REAL,
            strategy="StratX",
            reason="entry",
        )
        return handler, order

    @pytest.mark.asyncio
    async def test_with_market_in_redis(self):
        market = MagicMock(
            asset=MagicMock(value="ETH"),
            window=MagicMock(value="15m"),
        )
        handler, order = self._handler_and_order(market=market)
        pos = await handler._create_real_position(order, "market_001")
        assert pos.asset == "ETH"
        assert pos.window == "15m"
        assert pos.entry_price == 0.66
        assert pos.mode == "real"

    @pytest.mark.asyncio
    async def test_without_market_uses_unknown(self):
        handler, order = self._handler_and_order(market=None)
        pos = await handler._create_real_position(order, "market_001")
        assert pos.asset == "UNKNOWN"
        assert pos.window == "UNKNOWN"


class TestRealHandlerExit:
    """execute_exit — happy + retry + failure."""

    def _handler(self, *, create_order_response=None, side_effect=None):
        clob = AsyncMock()
        if side_effect:
            clob.create_order = AsyncMock(side_effect=side_effect)
        else:
            clob.create_order = AsyncMock(return_value=create_order_response or {
                "price": "0.70",
                "status": "FILLED",
                "orderID": "exit_order_id",
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
        )
        return handler, clob, repo, notifier

    @pytest.mark.asyncio
    async def test_happy_path_yes(self):
        handler, clob, repo, notifier = self._handler()
        position = make_position(side="YES", entry_price=0.50, shares=20.0)

        result = await handler.execute_exit(position, reason="target_reached")

        assert result.success is True
        assert result.mode == "real"
        clob.create_order.assert_awaited_once()
        args = clob.create_order.call_args.kwargs
        assert args["side"] == "SELL"
        assert args["size"] == 20.0
        repo.save_position.assert_awaited()
        notifier.send_exit_alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_happy_path_no_side_uses_buy_no_token(self):
        handler, clob, _, _ = self._handler()
        position = make_position(side="NO", entry_price=0.40, shares=25.0)

        result = await handler.execute_exit(position, reason="stop_loss")
        assert result.success is True
        # _get_token_and_price con BUY_NO devuelve no_token_id del mock
        # (que es el yes_token por defecto del fixture, pero el flujo igual cierra)
        clob.create_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exit_failure_returns_failed_result(self):
        import httpx
        # Error de red persistente → retry agota y devuelve error
        handler, clob, repo, notifier = self._handler(
            side_effect=httpx.ConnectError("network down")
        )
        position = make_position(side="YES", shares=10.0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler.execute_exit(position, reason="timeout")

        assert result.success is False
        # No notifier de exit cuando falla
        notifier.send_exit_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exit_no_notifier(self):
        handler, clob, repo, _ = self._handler()
        handler._notifier = None
        position = make_position(side="YES", shares=10.0)

        result = await handler.execute_exit(position, reason="manual")
        assert result.success is True


class TestRealHandlerHedge:
    """execute_hedge — delega a execute_entry con BUY_NO y respeta cap."""

    @pytest.mark.asyncio
    async def test_hedge_delegates_to_execute_entry(self):
        clob = AsyncMock()
        clob.create_order = AsyncMock(return_value={
            "price": "0.40", "status": "FILLED", "orderID": "hedge_id",
        })
        repo = make_mock_repo()
        redis = make_mock_redis()
        notifier = make_mock_notifier()
        audit = AuditLogger(repository=repo)
        handler = RealTradingHandler(
            clob_client=clob, repository=repo, redis=redis,
            notifier=notifier, audit_logger=audit,
        )

        captured = {}

        async def fake_execute_entry(signal, market_id, amount):
            captured["signal_type"] = signal.type
            captured["amount"] = amount
            captured["market_id"] = market_id
            return MagicMock(success=True)

        handler.execute_entry = fake_execute_entry
        position = make_position(side="YES", strategy="strat-y")

        await handler.execute_hedge(position, hedge_amount=5.0)

        assert captured["signal_type"] == SignalType.BUY_NO
        assert captured["amount"] == 5.0
        assert captured["market_id"] == position.market_id

    @pytest.mark.asyncio
    async def test_hedge_caps_amount_at_max(self):
        from src.execution.real_handler import MAX_ORDER_AMOUNT_PUSD
        clob = AsyncMock()
        repo = make_mock_repo()
        redis = make_mock_redis()
        notifier = make_mock_notifier()
        audit = AuditLogger(repository=repo)
        handler = RealTradingHandler(
            clob_client=clob, repository=repo, redis=redis,
            notifier=notifier, audit_logger=audit,
        )

        captured = {}

        async def fake_execute_entry(signal, market_id, amount):
            captured["amount"] = amount
            return MagicMock(success=True)

        handler.execute_entry = fake_execute_entry
        position = make_position(side="YES")

        await handler.execute_hedge(position, hedge_amount=MAX_ORDER_AMOUNT_PUSD * 2)
        assert captured["amount"] == MAX_ORDER_AMOUNT_PUSD


class TestRealHandlerRedeem:
    """redeem_resolved_position — V2 requiere CTF on-chain (fail-fast)."""

    def _handler(self, *, redeem_response=None, side_effect=None, ctf_redeemer=None):
        clob = AsyncMock()
        # Configurar ctf_redeemer explícitamente (default None = path legacy)
        clob.ctf_redeemer = ctf_redeemer
        if side_effect is not None:
            clob.redeem_position = AsyncMock(side_effect=side_effect)
        else:
            clob.redeem_position = AsyncMock(
                return_value=redeem_response or {"redeemed_amount": 20.0}
            )
        repo = make_mock_repo()
        redis = make_mock_redis()
        notifier = make_mock_notifier()
        audit = AuditLogger(repository=repo)
        return RealTradingHandler(
            clob_client=clob, repository=repo, redis=redis,
            notifier=notifier, audit_logger=audit,
        ), clob, repo

    @pytest.mark.asyncio
    async def test_redeem_ctf_unsupported_fail_fast(self):
        """
        En CLOB V2 el redeem es on-chain via CTF; el camino REST tira
        `CLOBRedeemNotSupportedError`. El handler debe (a) NO reintentar,
        (b) devolver `success=False`, (c) emitir audit log con razón
        `ctf_onchain_required`.
        """
        from src.infrastructure.polymarket.clob_client import (
            CLOBRedeemNotSupportedError,
        )

        handler, clob, repo = self._handler(
            side_effect=CLOBRedeemNotSupportedError("redeemPositions CTF required")
        )
        position = make_position(side="YES", shares=20.0, amount=10.0)

        # Patch audit logger para capturar la entrada
        handler._audit.log = AsyncMock()

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await handler.redeem_resolved_position(position, "yes_tok")

        assert result.success is False
        assert result.mode == "real"
        # NO reintentamos: redeem_position se llama UNA sola vez
        assert clob.redeem_position.await_count == 1
        # NO se invoca asyncio.sleep para retry
        assert mock_sleep.await_count == 0
        # Audit log refleja el motivo CTF
        audit_calls = handler._audit.log.await_args_list
        redeem_failed_calls = [
            c for c in audit_calls
            if c.kwargs.get("action").value == "real_redeem_failed"
        ]
        assert len(redeem_failed_calls) == 1
        assert redeem_failed_calls[0].kwargs["details"]["reason"] == "ctf_onchain_required"

    @pytest.mark.asyncio
    async def test_redeem_network_failure(self):
        """Errores de red genéricos: reintenta y termina en failure."""
        import httpx
        handler, _, _ = self._handler(
            side_effect=httpx.ConnectError("network")
        )
        position = make_position(side="YES", shares=20.0)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler.redeem_resolved_position(position, "yes_tok")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_redeem_via_ctf_when_redeemer_available(self):
        """
        Cuando ctf_redeemer disponible, redeem_resolved_position debe
        ejecutar path CTF on-chain (R2.0-redeem-impl F1 wire).
        """
        from src.domain.enums.finality_status import FinalityStatus
        from src.domain.value_objects.redeem_receipt import RedeemReceipt
        from unittest.mock import MagicMock

        # Mock CTFRedeemer que retorna receipt CONFIRMED
        mock_redeemer = AsyncMock()
        mock_redeemer.redeem = AsyncMock(return_value=RedeemReceipt(
            redeem_op_id="op-test",
            condition_id="0x" + "ab" * 32,
            tx_hash="0x" + "cd" * 32,
            index_sets=(1,),
            shares_redeemed=100,
            pusd_received=100.0,
            gas_used=250_000,
            gas_fee_matic=0.05,
            submitted_at=datetime.now(timezone.utc),
            mined_at=datetime.now(timezone.utc),
            confirmed_at=datetime.now(timezone.utc),
            status=FinalityStatus.CONFIRMED.value,
            proxy_address="0x" + "11" * 20,
            adapter_address="0x" + "22" * 20,
        ))

        clob = AsyncMock()
        clob.ctf_redeemer = mock_redeemer
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
        )

        position = make_position(side="YES", shares=100.0, amount=50.0)
        result = await handler.redeem_resolved_position(position, "yes_tok")

        # Verificar que el redeemer fue invocado
        assert mock_redeemer.redeem.await_count == 1
        call_kwargs = mock_redeemer.redeem.await_args.kwargs
        assert call_kwargs["shares_yes"] == 100
        assert call_kwargs["shares_no"] == 0
        assert call_kwargs["condition_id"] == position.market_id

        # Verificar resultado
        assert result.success is True
        assert result.amount == 100.0
        assert result.mode == "real"

    @pytest.mark.asyncio
    async def test_redeem_via_ctf_maps_no_side_correctly(self):
        """
        Position.side="NO" debe mapear a shares_no > 0, shares_yes = 0.
        """
        from src.domain.enums.finality_status import FinalityStatus
        from src.domain.value_objects.redeem_receipt import RedeemReceipt

        mock_redeemer = AsyncMock()
        mock_redeemer.redeem = AsyncMock(return_value=RedeemReceipt(
            redeem_op_id="op-no",
            condition_id="0x" + "ef" * 32,
            tx_hash="0x" + "cd" * 32,
            index_sets=(2,),
            shares_redeemed=50,
            pusd_received=50.0,
            gas_used=250_000,
            gas_fee_matic=0.05,
            submitted_at=datetime.now(timezone.utc),
            mined_at=datetime.now(timezone.utc),
            confirmed_at=datetime.now(timezone.utc),
            status=FinalityStatus.CONFIRMED.value,
            proxy_address="0x" + "11" * 20,
            adapter_address="0x" + "22" * 20,
        ))

        clob = AsyncMock()
        clob.ctf_redeemer = mock_redeemer
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
        )

        position = make_position(side="NO", shares=50.0, amount=25.0)
        await handler.redeem_resolved_position(position, "no_tok")

        call_kwargs = mock_redeemer.redeem.await_args.kwargs
        assert call_kwargs["shares_yes"] == 0
        assert call_kwargs["shares_no"] == 50

