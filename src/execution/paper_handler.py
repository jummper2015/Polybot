# src/execution/paper_handler.py

import math
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
from src.execution.slippage_engine import SlippageEngine
from src.execution.smart_router import SmartRouter
from src.infrastructure.cache.redis_client import RedisClient
from src.infrastructure.observability.metrics import (
    ORDERS_EXECUTED,
    PAPER_BALANCE_GAUGE,
    PAPER_POSITIONS_OPEN,
    PNL_GAUGE,
    QUEUE_ADVERSE_SELECTION_BPS,
    QUEUE_CONFIDENCE,
    QUEUE_MAKER_COST_RATIO,
    QUEUE_MAKER_EXPECTED_TIME,
    QUEUE_MAKER_FILL_PROBABILITY,
    QUEUE_MAKER_SAVINGS_PCT,
    QUEUE_MAKER_VS_TAKER_DECISIONS,
    QUEUE_TURNOVER_VOLUME_SEC,
    SLIPPAGE_ACTUAL_VS_ESTIMATED,
    SLIPPAGE_CALIBRATION,
    SLIPPAGE_ESTIMATED,
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
        execution_mode: str = "taker",
    ):
        self._repo     = repository
        self._redis    = redis
        self._notifier = notifier
        self._slippage = SlippageEngine()

        # Balance virtual — empieza con el inicial y se actualiza en cada trade
        self._balance  = initial_balance
        self._initial_balance = initial_balance

        # P9.3: Execution mode — "taker" (default), "maker", or "auto"
        self._execution_mode = execution_mode

        # P9.4: Smart routing (optional) — orchestrates P9.1-P9.3
        self._router = SmartRouter(slippage_engine=self._slippage)

        logger.info(
            "paper_handler_initialized",
            initial_balance=initial_balance,
            fill_model="P9.2 SlippageEngine",
            tracker_calibration=self._slippage.calibration_multiplier,
            execution_mode=execution_mode,
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
        tick_data = await self._build_tick_data(market_id)

        # ── Modelo de slippage (P9.2 SlippageEngine) ───────────────────
        # TODO(P9.2): compute realized volatility from Redis tick history
        # TODO(P9.2): query RegimeClassifier (P8.4) for current regime
        estimate = self._slippage.estimate(
            tick_data=tick_data,
            order_size=amount,
            asset=self._get_asset_for_market(market_id),
            side="entry",
            volatility=None,
            regime=None,
        )

        # ── P9.3: Resolve maker-vs-taker fill ──────────────────────────
        (
            fill_price, slippage, exec_mode, maker_est, decision
        ) = self._resolve_fill(
            tick_data=tick_data,
            order_size=amount,
            side="entry",
            taker_estimate=estimate,
        )

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

        # ── Calibrar tracker de slippage con el fill real ─────────────
        self._slippage.record_actual(estimate, actual_fill_price=fill_price)

        # ── Persiste en DB ────────────────────────────────────────────
        await self._repo.save_order(order)
        await self._repo.save_position(position)

        # ── Métricas ──────────────────────────────────────────────────
        ORDERS_EXECUTED.labels(mode="paper", side=order.side.value).inc()
        PAPER_BALANCE_GAUGE.set(self._balance)
        PAPER_POSITIONS_OPEN.inc()
        SLIPPAGE_ESTIMATED.labels(mode="paper", side="entry").observe(
            abs(estimate.adjusted_slippage)
        )
        # Note: in paper trading, estimated == actual (same model).
        # The ratio will always be ~1.0. This metric becomes meaningful
        # in real trading where actual fill differs from estimate.
        mid = (tick_data["best_bid"] + tick_data["best_ask"]) / 2
        actual_slip = abs(fill_price - mid)
        if abs(estimate.adjusted_slippage) > 0:
            SLIPPAGE_ACTUAL_VS_ESTIMATED.labels(
                mode="paper", side="entry"
            ).observe(actual_slip / abs(estimate.adjusted_slippage))
        SLIPPAGE_CALIBRATION.labels(mode="paper").set(
            self._slippage.calibration_multiplier
        )

        # ── Queue Position metrics (P9.3) ─────────────────────────────
        self._observe_queue_metrics(
            side="entry",
            maker_estimate=maker_est,
            decision=decision,
        )

        log.info(
            "paper_order_filled",
            order_id=order.id,
            side=order.side.value,
            fill_price=fill_price,
            slippage=round(slippage, 6),
            fill_ratio=estimate.fill_ratio,
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

        # Precio actual y tick data para calcular exit slippage
        current_price = await self._get_current_yes_price(position.market_id)
        tick_data = await self._build_tick_data(position.market_id)
        position_value = position.shares * current_price

        # ── Modelo de slippage (P9.2 SlippageEngine) ───────────────────
        asset = position.asset if position.asset != "UNKNOWN" else "DEFAULT"
        estimate = self._slippage.estimate(
            tick_data=tick_data,
            order_size=position_value,
            asset=asset,
            side="exit",
            volatility=None,
            regime=None,
        )

        # ── P9.3: Resolve maker-vs-taker fill ──────────────────────────
        (
            exit_price, slippage, exec_mode, maker_est, decision
        ) = self._resolve_fill(
            tick_data=tick_data,
            order_size=position_value,
            side="exit",
            taker_estimate=estimate,
        )

        # ── Cierra la posición y calcula PnL ─────────────────────────
        position.close(exit_price=exit_price, reason=reason)

        # ── Devuelve el valor al balance virtual ──────────────────────
        # Valor de retorno = shares * exit_price
        return_value  = position.shares * exit_price
        self._balance += return_value
        await self._redis.set_paper_balance(self._balance)

        # ── Calibrar tracker de slippage con el fill real ─────────────
        self._slippage.record_actual(estimate, actual_fill_price=exit_price)

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
            slippage     = slippage,
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
        SLIPPAGE_ESTIMATED.labels(mode="paper", side="exit").observe(
            abs(estimate.adjusted_slippage)
        )
        # Note: in paper trading, estimated == actual (same model).
        mid_exit = (tick_data["best_bid"] + tick_data["best_ask"]) / 2
        actual_slip_exit = abs(exit_price - mid_exit)
        if abs(estimate.adjusted_slippage) > 0:
            SLIPPAGE_ACTUAL_VS_ESTIMATED.labels(
                mode="paper", side="exit"
            ).observe(actual_slip_exit / abs(estimate.adjusted_slippage))
        SLIPPAGE_CALIBRATION.labels(mode="paper").set(
            self._slippage.calibration_multiplier
        )

        # ── Queue Position metrics (P9.3) ─────────────────────────────
        self._observe_queue_metrics(
            side="exit",
            maker_estimate=maker_est,
            decision=decision,
        )

        log.info(
            "paper_position_closed",
            position_id=position.id,
            exit_price=exit_price,
            slippage=round(slippage, 6),
            fill_ratio=estimate.fill_ratio,
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
            slippage     = slippage,
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
        Simula la compra de NO como cobertura parcial de una posicion YES.
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

        # ── Slippage via SlippageEngine (P9.2) ─────────────────────────
        tick_data = await self._build_tick_data(position.market_id)
        asset = position.asset if position.asset != "UNKNOWN" else "DEFAULT"
        estimate = self._slippage.estimate(
            tick_data=tick_data,
            order_size=hedge_amount,
            asset=asset,
            side="entry",
            volatility=None,
            regime=None,
        )
        # Hedge buys NO, which is the complement of YES.
        # SlippageEngine gives us the YES-side fill price; NO fill = 1 - YES fill.
        # For simplicity, use the NO price + entry slippage as before.
        slippage   = estimate.slippage
        fill_price = round(no_price + slippage, 4)
        fill_price = min(fill_price, 0.999)

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

    def _resolve_fill(
        self,
        tick_data: dict,
        order_size: float,
        side: str,
        taker_estimate,
    ) -> tuple[float, float, str, object | None, object | None]:
        """Resolve fill price and slippage based on execution_mode.

        P9.3: When mode is "maker" or "auto", estimates the maker fill
        probability and compares expected costs. If maker is preferred,
        fills at mid_price with zero spread crossing (but risks partial
        fill based on P(fill)).

        P9.4: When mode is "auto", delegates to SmartRouter for
        adaptive thresholds + order splitting.

        Args:
            tick_data: Orderbook tick dict.
            order_size: Order size in USDC.
            side: "entry" or "exit".
            taker_estimate: SlippageEstimate from the taker path.

        Returns:
            (fill_price, slippage, exec_mode, maker_estimate, decision)
              maker_estimate and decision are None for taker-only path.
        """
        mode = self._execution_mode

        if mode == "taker":
            return (
                taker_estimate.fill_price,
                taker_estimate.slippage,
                "taker",
                None,
                None,
            )

        # ── P9.4: Smart Router (auto mode) ─────────────────────────
        if mode == "auto":
            routing = self._router.route(
                tick_data=tick_data,
                order_size=order_size,
                side=side,
                volatility=None,
                regime=None,
            )
            # For now, use first chunk's mode and fill
            # (split handling in paper trading is simplified)
            chunk = routing.chunks[0]
            if chunk.mode == "maker":
                mid = (tick_data["best_bid"] + tick_data["best_ask"]) / 2
                fill_ratio = max(0.1, routing.maker_p_fill)
                unfilled = 1.0 - fill_ratio
                maker_fill_price = (
                    fill_ratio * mid + unfilled * taker_estimate.fill_price
                )
                maker_fill_price = max(0.001, min(0.999, maker_fill_price))
                maker_slippage = round(maker_fill_price - mid, 6)
                return (
                    round(maker_fill_price, 6),
                    maker_slippage,
                    "maker",
                    None,  # maker_est not cached from router
                    None,  # decision not cached from router
                )
            else:
                return (
                    taker_estimate.fill_price,
                    taker_estimate.slippage,
                    "taker",
                    None,
                    None,
                )

        # ── P9.3: Maker mode ───────────────────────────────────────
        maker = self._slippage.estimate_maker(
            tick_data=tick_data,
            order_size=order_size,
            side=side,
            volatility=None,
            regime=None,
        )
        taker_cost = abs(taker_estimate.adjusted_slippage)
        decision = self._slippage.compare_maker_vs_taker(
            taker_cost=taker_cost,
            maker_estimate=maker,
        )

        if decision.prefer_maker:
            mid = (tick_data["best_bid"] + tick_data["best_ask"]) / 2
            fill_ratio = max(0.1, maker.p_fill)
            unfilled = 1.0 - fill_ratio
            maker_fill_price = (
                fill_ratio * mid + unfilled * taker_estimate.fill_price
            )
            maker_fill_price = max(0.001, min(0.999, maker_fill_price))
            maker_slippage = round(maker_fill_price - mid, 6)
            return (
                round(maker_fill_price, 6),
                maker_slippage,
                "maker",
                maker,
                decision,
            )

        # Maker mode but maker not preferred -> fallback to taker
        return (
            taker_estimate.fill_price,
            taker_estimate.slippage,
            "taker",
            maker,
            decision,
        )

    def _observe_queue_metrics(
        self,
        side: str,
        maker_estimate,
        decision,
        taker_cost: float = 0.0,
        volatility: float | None = None,
        regime: str | None = None,
    ) -> None:
        """Observe P9.3 Queue Position metrics from pre-computed data.

        Accepts maker estimate and decision objects already computed
        by _resolve_fill() to avoid duplicate estimation work.

        When called from a taker-only path (maker_estimate/decision are
        None), computes them once here for metric observation only.
        """
        if maker_estimate is None or decision is None:
            # Taker-only path: compute once for metrics
            return  # No-op: taker path doesn't need queue metrics

        regime_label = (regime or "UNKNOWN").lower()

        QUEUE_MAKER_FILL_PROBABILITY.labels(
            side=side, regime=regime_label,
        ).observe(maker_estimate.p_fill)

        if maker_estimate.expected_time_to_fill != float("inf"):
            QUEUE_MAKER_EXPECTED_TIME.labels(side=side).observe(
                maker_estimate.expected_time_to_fill
            )

        QUEUE_ADVERSE_SELECTION_BPS.labels(
            side=side, regime=regime_label,
        ).observe(maker_estimate.adverse_selection_bps)

        QUEUE_MAKER_VS_TAKER_DECISIONS.labels(
            mode=decision.mode, side=side,
        ).inc()

        if not math.isinf(decision.cost_ratio):
            QUEUE_MAKER_COST_RATIO.labels(side=side).observe(
                decision.cost_ratio
            )

        QUEUE_MAKER_SAVINGS_PCT.labels(side=side).observe(
            decision.savings_pct
        )

        QUEUE_TURNOVER_VOLUME_SEC.labels(asset="DEFAULT").observe(
            maker_estimate.volume_sec
        )

        QUEUE_CONFIDENCE.labels(side=side).observe(maker_estimate.confidence)

    async def _get_target_price(
        self, signal: Signal, market_id: str
    ) -> float:
        """
        Obtiene el precio objetivo desde Redis (último tick conocido).
        Fallback a 0.5 si no hay datos.
        """
        tick = await self._redis.get_last_tick_price(market_id)
        if tick and tick.get("last_yes_price"):
            return float(tick["last_yes_price"])

        logger.warning("no_price_in_redis", market_id=market_id)
        return 0.5

    async def _get_current_yes_price(self, market_id: str) -> float:
        """Precio YES actual desde Redis."""
        tick = await self._redis.get_last_tick_price(market_id)
        if tick and tick.get("last_yes_price"):
            return float(tick["last_yes_price"])
        return 0.5

    async def _build_tick_data(self, market_id: str) -> dict:
        """
        Build a FillSimulator-compatible tick dict from Redis data.

        Priority:
        1. Orderbook data (bids/asks with depth via get_orderbook)
        2. Last tick price (yes_price, spread, best_bid/best_ask)
        3. Sensible defaults
        """
        # Try orderbook first (has real depth data)
        ob = await self._redis.get_orderbook(market_id)
        if ob:
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            if bids and asks:
                best_bid = max(float(b["price"]) for b in bids)
                best_ask = min(float(a["price"]) for a in asks)
                spread = best_ask - best_bid

                # Top-3 bid volumes
                bids_sorted = sorted(bids, key=lambda b: float(b["price"]), reverse=True)
                asks_sorted = sorted(asks, key=lambda a: float(a["price"]))

                def _vol(items, idx):
                    return float(items[idx].get("size", 0)) if idx < len(items) else 0.0

                return {
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread": spread,
                    "bids_vol_1": _vol(bids_sorted, 0),
                    "bids_vol_2": _vol(bids_sorted, 1),
                    "bids_vol_3": _vol(bids_sorted, 2),
                    "asks_vol_1": _vol(asks_sorted, 0),
                    "asks_vol_2": _vol(asks_sorted, 1),
                    "asks_vol_3": _vol(asks_sorted, 2),
                    "volume_24h": 0.0,
                }

        # Fallback: last tick price (computes bid/ask from yes_price ± spread/2)
        tick = await self._redis.get_last_tick_price(market_id)
        if tick:
            yes_price = float(tick.get("last_yes_price", 0.5))
            spread = float(tick.get("last_spread", 0.02))
            best_bid = float(tick.get("best_bid", yes_price - spread / 2))
            best_ask = float(tick.get("best_ask", yes_price + spread / 2))
            vol = float(tick.get("volume_24h", 0))
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "bids_vol_1": 0.0, "bids_vol_2": 0.0, "bids_vol_3": 0.0,
                "asks_vol_1": 0.0, "asks_vol_2": 0.0, "asks_vol_3": 0.0,
                "volume_24h": vol,
            }

        # Ultimate fallback
        return {
            "best_bid": 0.49, "best_ask": 0.51, "spread": 0.02,
            "bids_vol_1": 0.0, "bids_vol_2": 0.0, "bids_vol_3": 0.0,
            "asks_vol_1": 0.0, "asks_vol_2": 0.0, "asks_vol_3": 0.0,
            "volume_24h": 0.0,
        }

    @staticmethod
    def _get_asset_for_market(market_id: str) -> str:
        """Infer asset from market context.

        The market_id alone doesn't encode the asset.
        Returns DEFAULT profile (safe BTC/ETH middle-ground).

        TODO: pass asset from Signal or Market context once available
              in the entry path (exit path already has position.asset).
        """
        return "DEFAULT"

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

    @property
    def execution_mode(self) -> str:
        """P9.3: Current execution mode — "taker", "maker", or "auto"."""
        return self._execution_mode

    @execution_mode.setter
    def execution_mode(self, value: str) -> None:
        if value not in ("taker", "maker", "auto"):
            raise ValueError(
                f"execution_mode must be 'taker', 'maker', or 'auto', got '{value}'"
            )
        self._execution_mode = value
        logger.info("paper_execution_mode_changed", execution_mode=value)
