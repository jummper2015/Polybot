# src/execution/paper_handler.py

import uuid
from datetime import datetime

import structlog
from opentelemetry import trace

from src.application.ports.notification_port import INotificationPort
from src.application.ports.repository_port import IRepositoryPort
from src.domain.entities.order import Order
from src.domain.entities.position import Position
from src.domain.enums.order_side import OrderSide
from src.domain.enums.order_status import OrderStatus
from src.domain.enums.trading_mode import TradingMode
from src.domain.value_objects.signal import Signal, SignalType
from src.domain.value_objects.trade_result import TradeResult
from src.execution.base import IExecutionHandler
from src.infrastructure.cache.redis_client import RedisClient
from src.infrastructure.observability.metrics import (
    ORDERS_EXECUTED,
    PAPER_BALANCE_GAUGE,
    PAPER_POSITIONS_OPEN,
    PNL_GAUGE,
)
from src.infrastructure.observability.tracing import get_tracer

logger = structlog.get_logger(__name__)

# Modo fijo de este handler
TRADING_MODE = TradingMode.PAPER


class PaperTradingHandler(IExecutionHandler):
    """
    Simula ejecución de órdenes sin operar dinero real.
    Implementa el mismo contrato IExecutionHandler que el handler real.
    Calcula slippage estimado, actualiza balance virtual y persiste en DB.
    """

    def __init__(
        self,
        repository:    IRepositoryPort,
        redis:         RedisClient,
        notifier:      INotificationPort,
        initial_balance: float = 1000.0,
    ):
        self._repo     = repository
        self._redis    = redis
        self._notifier = notifier

        # Balance virtual — empieza con el inicial y se actualiza en cada trade
        self._balance  = initial_balance
        self._initial_balance = initial_balance

        logger.info(
            "paper_handler_initialized",
            initial_balance=initial_balance,
        )

    # ------------------------------------------------------------------
    # IExecutionHandler — Contrato
    # ------------------------------------------------------------------

    async def execute_entry(
        self,
        signal:    Signal,
        market_id: str,
        amount:    float,
    ) -> TradeResult:
        """
        Simula la compra de YES o NO en un mercado.
        1. Calcula slippage desde el spread
        2. Calcula fill price realista
        3. Descuenta del balance virtual
        4. Crea Order + Position y persiste en DB
        5. Notifica al usuario
        """
        log = logger.bind(
            market_id=market_id,
            signal_type=signal.type.value,
            amount=amount,
            mode="paper",
        )
        get_tracer()
        trace.get_current_span()

        # Obtiene el tick actual para calcular slippage
        target_price = await self._get_target_price(signal, market_id)
        spread       = await self._get_current_spread(market_id)

        # ── Modelo de slippage ────────────────────────────────────────
        # Slippage = 50% del spread (costo de cruzar el book)
        slippage   = spread * 0.5
        fill_price = round(target_price + slippage, 4)  # Compramos al ask estimado
        fill_price = min(fill_price, 0.999)              # Cap: nunca > 0.999

        # ── Verificación de balance ───────────────────────────────────
        if amount > self._balance:
            error_msg = (
                f"balance insuficiente: "
                f"requested={amount:.2f} > available={self._balance:.2f} USDC"
            )
            log.warning("paper_insufficient_balance", error=error_msg)
            return self._failed_result(market_id, signal, amount, target_price, error_msg)

        # ── Crea la orden ─────────────────────────────────────────────
        order = Order(
            id           = str(uuid.uuid4()),
            market_id    = market_id,
            side         = OrderSide.YES if signal.type == SignalType.BUY_YES else OrderSide.NO,
            amount       = amount,
            target_price = target_price,
            fill_price   = None,
            slippage     = None,
            status       = OrderStatus.PENDING,
            mode         = TRADING_MODE,
            strategy     = signal.source_strategy,
            reason       = signal.reason,
        )

        # ── Simula el fill ────────────────────────────────────────────
        order.mark_filled(fill_price=fill_price, slippage=slippage)

        shares = order.shares  # amount / fill_price

        # ── Crea la posición ──────────────────────────────────────────
        position = await self._create_position(
            order=order,
            market_id=market_id,
            shares=shares,
        )

        # ── Actualiza balance virtual ─────────────────────────────────
        self._balance -= amount
        await self._redis.set_paper_balance(self._balance)

        # ── Persiste en DB ────────────────────────────────────────────
        await self._repo.save_order(order)
        await self._repo.save_position(position)

        # ── Métricas ──────────────────────────────────────────────────
        ORDERS_EXECUTED.labels(mode="paper", side=order.side.value).inc()
        PAPER_BALANCE_GAUGE.set(self._balance)
        PAPER_POSITIONS_OPEN.inc()

        log.info(
            "paper_order_filled",
            order_id=order.id,
            side=order.side.value,
            fill_price=fill_price,
            slippage=slippage,
            shares=round(shares, 4),
            balance_after=round(self._balance, 2),
        )

        # ── Notifica al usuario ───────────────────────────────────────
        if self._notifier is not None:
            await self._notifier.send_trade_alert(
                market_id=market_id,
                side=order.side.value,
                amount=amount,
                price=fill_price,
                mode="paper",
            )

        return TradeResult(
            order_id     = order.id,
            market_id    = market_id,
            side         = order.side.value,
            amount       = amount,
            target_price = target_price,
            fill_price   = fill_price,
            slippage     = slippage,
            pnl          = None,      # PnL se realiza al cerrar
            success      = True,
            mode         = "paper",
            timestamp    = datetime.utcnow(),
        )

    async def execute_exit(
        self,
        position: Position,
        reason:   str,
    ) -> TradeResult:
        """
        Simula el cierre de una posición existente.
        El precio de venta es el precio actual MENOS el slippage
        (vendemos al bid estimado).
        """
        log = logger.bind(
            position_id=position.id,
            market_id=position.market_id,
            reason=reason,
            mode="paper",
        )
        get_tracer()
        trace.get_current_span()

        # Precio actual y spread para calcular exit price
        current_price = await self._get_current_yes_price(position.market_id)
        spread        = await self._get_current_spread(position.market_id)

        # Al vender: obtenemos bid estimado (precio - 50% spread)
        slippage   = spread * 0.5
        exit_price = round(current_price - slippage, 4)
        exit_price = max(exit_price, 0.001)  # Floor: nunca < 0.001

        # ── Cierra la posición y calcula PnL ─────────────────────────
        position.close(exit_price=exit_price, reason=reason)

        # ── Devuelve el valor al balance virtual ──────────────────────
        # Valor de retorno = shares * exit_price
        return_value  = position.shares * exit_price
        self._balance += return_value
        await self._redis.set_paper_balance(self._balance)

        # ── Persiste posición cerrada ─────────────────────────────────
        await self._repo.save_position(position)

        # ── Crea orden de cierre para auditoría ──────────────────────
        exit_order = Order(
            id           = str(uuid.uuid4()),
            market_id    = position.market_id,
            side         = position.side,
            amount       = return_value,
            target_price = current_price,
            fill_price   = exit_price,
            slippage     = -slippage,   # Negativo porque lo perdemos al vender
            status       = OrderStatus.FILLED,
            mode         = TRADING_MODE,
            strategy     = position.strategy,
            reason       = f"exit: {reason}",
        )
        exit_order.filled_at = datetime.utcnow()
        await self._repo.save_order(exit_order)

        # ── Métricas ──────────────────────────────────────────────────
        ORDERS_EXECUTED.labels(mode="paper", side="EXIT").inc()
        PAPER_BALANCE_GAUGE.set(self._balance)
        PAPER_POSITIONS_OPEN.dec()
        PNL_GAUGE.labels(mode="paper").set(
            self._balance - self._initial_balance
        )

        log.info(
            "paper_position_closed",
            position_id=position.id,
            exit_price=exit_price,
            pnl=round(position.pnl, 4),
            pnl_pct=f"{position.pnl_pct:.2%}",
            balance_after=round(self._balance, 2),
            reason=reason,
        )

        # ── Notifica al usuario ───────────────────────────────────────
        if self._notifier is not None:
            await self._notifier.send_exit_alert(
                market_id=position.market_id,
                reason=reason,
                pnl=position.pnl,
                pnl_pct=position.pnl_pct,
            )

        return TradeResult(
            order_id     = exit_order.id,
            market_id    = position.market_id,
            side         = position.side,
            amount       = return_value,
            target_price = current_price,
            fill_price   = exit_price,
            slippage     = -slippage,
            pnl          = position.pnl,
            success      = True,
            mode         = "paper",
            timestamp    = datetime.utcnow(),
        )

    async def execute_hedge(
        self,
        position:     Position,
        hedge_amount: float,
    ) -> TradeResult:
        """
        Simula la compra de NO como cobertura parcial de una posición YES.
        Trata el hedge como una entrada nueva en el lado opuesto.
        """
        log = logger.bind(
            position_id=position.id,
            market_id=position.market_id,
            hedge_amount=hedge_amount,
            mode="paper",
        )

        current_price = await self._get_current_yes_price(position.market_id)
        no_price      = 1.0 - current_price   # Precio NO = 1 - precio YES
        spread        = await self._get_current_spread(position.market_id)
        slippage      = spread * 0.5
        fill_price    = round(no_price + slippage, 4)
        fill_price    = min(fill_price, 0.999)

        if hedge_amount > self._balance:
            hedge_amount = self._balance * 0.5  # Reduce al 50% del balance disponible

        # Crea orden de hedge
        hedge_order = Order(
            id           = str(uuid.uuid4()),
            market_id    = position.market_id,
            side         = "NO",
            amount       = hedge_amount,
            target_price = no_price,
            fill_price   = None,
            slippage     = None,
            status       = OrderStatus.PENDING,
            mode         = TRADING_MODE,
            strategy     = position.strategy,
            reason       = f"hedge for position {position.id}",
        )
        hedge_order.mark_filled(fill_price=fill_price, slippage=slippage)

        self._balance -= hedge_amount
        await self._redis.set_paper_balance(self._balance)
        await self._repo.save_order(hedge_order)

        log.info(
            "paper_hedge_executed",
            order_id=hedge_order.id,
            no_price=no_price,
            fill_price=fill_price,
            hedge_amount=hedge_amount,
        )

        ORDERS_EXECUTED.labels(mode="paper", side="NO").inc()

        return TradeResult(
            order_id     = hedge_order.id,
            market_id    = position.market_id,
            side         = "NO",
            amount       = hedge_amount,
            target_price = no_price,
            fill_price   = fill_price,
            slippage     = slippage,
            pnl          = None,
            success      = True,
            mode         = "paper",
            timestamp    = datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # HELPERS INTERNOS
    # ------------------------------------------------------------------

    async def _get_target_price(
        self, signal: Signal, market_id: str
    ) -> float:
        """
        Obtiene el precio objetivo desde Redis (último tick conocido).
        Fallback a 0.5 si no hay datos (no debería ocurrir en operación normal).
        """
        state = await self._redis.get_ws_state(market_id)
        if state and state.get("last_yes_price"):
            return float(state["last_yes_price"])

        # Fallback: usa el mid price como aproximación
        logger.warning("no_price_in_redis", market_id=market_id)
        return 0.5

    async def _get_current_yes_price(self, market_id: str) -> float:
        """Precio YES actual desde Redis."""
        state = await self._redis.get_ws_state(market_id)
        if state and state.get("last_yes_price"):
            return float(state["last_yes_price"])
        return 0.5

    async def _get_current_spread(self, market_id: str) -> float:
        """Spread actual desde Redis. Fallback conservador de 0.02 (2%)."""
        state = await self._redis.get_ws_state(market_id)
        if state and state.get("last_spread"):
            return float(state["last_spread"])
        return 0.02  # 2% como estimación conservadora

    async def _create_position(
        self,
        order:     Order,
        market_id: str,
        shares:    float,
    ) -> Position:
        """Crea una entidad Position a partir de una orden filled."""
        market = await self._redis.get_market(market_id)
        return Position(
            id          = str(uuid.uuid4()),
            market_id   = market_id,
            asset       = market.asset.value if market else "UNKNOWN",
            window      = market.window.value if market else "UNKNOWN",
            side        = order.side.value,
            amount      = order.amount,
            shares      = shares,
            entry_price = order.fill_price,
            exit_price  = None,
            pnl         = None,
            pnl_pct     = None,
            mode        = "paper",
            strategy    = order.strategy,
            exit_reason = None,
        )

    def _failed_result(
        self,
        market_id:    str,
        signal:       Signal,
        amount:       float,
        target_price: float,
        error:        str,
    ) -> TradeResult:
        """Construye un TradeResult de fallo sin persistir nada."""
        return TradeResult(
            order_id     = str(uuid.uuid4()),
            market_id    = market_id,
            side         = signal.type.value,
            amount       = amount,
            target_price = target_price,
            fill_price   = target_price,
            slippage     = 0.0,
            pnl          = None,
            success      = False,
            mode         = "paper",
            timestamp    = datetime.utcnow(),
            error        = error,
        )

    # ------------------------------------------------------------------
    # CONSULTAS DE ESTADO
    # ------------------------------------------------------------------

    def get_balance(self) -> float:
        """Balance virtual actual."""
        return self._balance

    def get_total_pnl(self) -> float:
        """PnL total = balance actual - balance inicial."""
        return self._balance - self._initial_balance

    def get_total_pnl_pct(self) -> float:
        """PnL total en porcentaje sobre el balance inicial."""
        if self._initial_balance <= 0:
            return 0.0
        return self.get_total_pnl() / self._initial_balance
