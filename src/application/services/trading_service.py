# src/application/services/trading_service.py

import asyncio
from datetime import datetime

import structlog

from src.application.ports.notification_port import INotificationPort
from src.application.ports.repository_port import IRepositoryPort
from src.application.services.market_service import MarketService
from src.application.services.portfolio_service import PortfolioService
from src.domain.entities.market import Market
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal
from src.execution.base import IExecutionHandler
from src.execution.liquidity_sizer import LiquidityAwareSizer
from src.infrastructure.observability.metrics import (
    CYCLE_DURATION,
    CYCLE_ERRORS,
    LIQUIDITY_DEPTH_COVERAGE,
    LIQUIDITY_MULTIPLIER,
    LIQUIDITY_SIZE_REDUCTIONS,
    LIQUIDITY_SPREAD_FACTOR,
    LIQUIDITY_VOLUME_FACTOR,
    SIGNALS_GENERATED,
)
from src.infrastructure.observability.tracing import get_tracer
from src.risk.engine import RiskEngine
from src.strategies.engine import StrategyEngine

logger = structlog.get_logger(__name__)

# Intervalo entre ciclos de cada mercado (segundos)
CYCLE_INTERVAL_SECONDS = 30

# Intervalo de re-discovery de mercados (segundos)
DISCOVERY_INTERVAL_SECONDS = 3600


class TradingService:
    """
    Caso de uso principal: orquesta el ciclo completo de trading.
    Arranca/detiene el bot, gestiona el timer y delega a Strategy/Risk/Execution.
    """

    def __init__(
        self,
        market_service:     MarketService,
        strategy_engine:    StrategyEngine,
        risk_engine:        RiskEngine,
        execution_handler:  IExecutionHandler,
        repository:         IRepositoryPort,
        notifier:           INotificationPort,
        portfolio_service:  PortfolioService,
        position_size_pusd: float = 10.0,
        trading_mode:       str   = "paper",
    ):
        self._market_svc   = market_service
        self._strategy     = strategy_engine
        self._risk         = risk_engine
        self._execution    = execution_handler
        self._repo         = repository
        self._notifier     = notifier
        self._portfolio    = portfolio_service
        self._position_size = position_size_pusd
        self._trading_mode  = trading_mode

        # P11.3: Liquidity-aware position sizing
        self._liquidity_sizer = LiquidityAwareSizer()

        self._running      = False
        self._tasks:       list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # CONTROL DEL BOT
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Arranca el bot:
        1. Discovery inicial de mercados
        2. Lanza el loop de ciclos
        3. Lanza el re-discovery periódico
        """
        if self._running:
            logger.warning("bot_already_running")
            return

        self._running = True
        logger.info("bot_starting")

        # Discovery inicial antes de empezar a operar
        await self._market_svc.discover_markets()

        # Subscribe WebSockets para recibir orderbook en tiempo real
        await self._market_svc.subscribe_all_to_orderbook(self._on_ws_tick)

        # Lanza las dos tareas en paralelo
        self._tasks = [
            asyncio.create_task(self._market_cycle_loop(),   name="market_cycle"),
            asyncio.create_task(self._rediscovery_loop(),    name="rediscovery"),
        ]

        logger.info("bot_started")

    async def stop(self) -> None:
        """
        Detiene el bot limpiamente:
        Cancela tareas pendientes y espera a que terminen.
        """
        self._running = False
        logger.info("bot_stopping")

        for task in self._tasks:
            task.cancel()

        # Espera cancelación sin propagar CancelledError
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        logger.info("bot_stopped")

    async def get_status(self) -> dict:
        """Devuelve estado actual del bot para el endpoint /health y Telegram."""
        markets = await self._market_svc.get_active_markets()
        return {
            "running":         self._running,
            "active_markets":  len(markets),
            "tasks_running":   sum(1 for t in self._tasks if not t.done()),
            "timestamp":       datetime.utcnow().isoformat(),
        }

    # ------------------------------------------------------------------
    # WEBSOCKET CALLBACK
    # ------------------------------------------------------------------

    async def _on_ws_tick(self, tick: MarketTick) -> None:
        """
        Callback invocado por el WebSocket en cada tick recibido.
        Actualiza los precios en DB/Redis para que el dashboard
        muestre datos en tiempo real sin esperar al ciclo de trading.
        """
        try:
            await self._market_svc.update_market_prices(
                market_id=tick.market_id,
                yes_price=tick.yes_price,
                no_price=tick.no_price,
                volume=tick.volume_24h,
            )
        except Exception as e:
            logger.debug(
                "ws_tick_update_failed",
                market_id=tick.market_id,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # LOOPS INTERNOS
    # ------------------------------------------------------------------

    async def _market_cycle_loop(self) -> None:
        """
        Loop principal: cada 30 segundos corre un ciclo
        para cada mercado activo descubierto.
        """
        while self._running:
            try:
                markets = await self._market_svc.get_active_markets()
                active  = [m for m in markets if m.is_active()]

                logger.debug("cycle_tick", active_markets=len(active))

                # Procesa todos los mercados en paralelo
                await asyncio.gather(
                    *[self._run_market_cycle(market) for market in active],
                    return_exceptions=True,
                )

            except Exception as e:
                logger.error("cycle_loop_error", error=str(e))
                CYCLE_ERRORS.inc()

            await asyncio.sleep(CYCLE_INTERVAL_SECONDS)

    async def _rediscovery_loop(self) -> None:
        """
        Loop secundario: re-descubre mercados cada 60 minutos
        para capturar nuevos mercados que Polymarket publique.
        """
        while self._running:
            await asyncio.sleep(DISCOVERY_INTERVAL_SECONDS)
            try:
                logger.info("rediscovery_triggered")
                await self._market_svc.discover_markets()
            except Exception as e:
                logger.error("rediscovery_error", error=str(e))

    # ------------------------------------------------------------------
    # CICLO POR MERCADO
    # ------------------------------------------------------------------

    async def _run_market_cycle(self, market: Market) -> None:
        """
        Ejecuta un ciclo completo de trading para un mercado específico:
        1. Inicia ciclo en strategy engine
        2. Obtiene tick actual
        3. Evalúa salida (si hay posición abierta)
        4. Evalúa entrada (si no hay posición) → riesgo → ejecución
        5. Finaliza ciclo en strategy engine (siempre, incluso en error)

        Instrumentado con OpenTelemetry: span raíz con sub-spans anidados
        para strategy_evaluation, risk_evaluation y execution.
        """
        log = logger.bind(
            market_id=market.id,
            asset=market.asset.value,
        )
        tracer = get_tracer()

        # ── Span raíz: market_cycle ───────────────────────────────────
        with tracer.start_as_current_span("market_cycle") as cycle_span:
            cycle_span.set_attribute("market.id", market.id)
            cycle_span.set_attribute("market.asset", market.asset.value)
            cycle_span.set_attribute("market.window", market.window.value)

            try:
                with CYCLE_DURATION.labels(
                    asset=market.asset.value,
                    window=market.window.value,
                ).time():
                    # ── 1. Inicio de ciclo ────────────────────────────
                    await self._strategy.on_cycle_start(market)

                    # ── 2. Obtener tick actual ────────────────────────
                    with tracer.start_as_current_span("fetch_tick") as tick_span:
                        tick = await self._get_current_tick(market)
                        if tick is None:
                            log.debug("no_tick_available")
                            return
                        tick_span.set_attribute("tick.yes_price", str(tick.yes_price))
                        tick_span.set_attribute("tick.spread", str(tick.spread))

                    # ── 3. Procesar tick ───────────────────────────────
                    await self._strategy.on_tick(market, tick)

                    # ── 4. Evaluar salida (posición existente) ────────
                    with tracer.start_as_current_span("strategy_evaluation") as strat_span:
                        strat_span.set_attribute("evaluation_type", "exit")
                        exit_signal: Signal = await self._strategy.should_exit(
                            market, tick
                        )

                    if exit_signal.is_actionable():
                        log.info(
                            "exit_signal_detected",
                            signal_type=exit_signal.type.value,
                            reason=exit_signal.reason,
                        )
                        await self._execute_exit(market, exit_signal)
                        return  # No evaluar entrada si acabamos de salir

                    # ── 5. Evaluar entrada (sin posición) ─────────────
                    with tracer.start_as_current_span("strategy_evaluation") as strat_span:
                        strat_span.set_attribute("evaluation_type", "entry")
                        entry_signal: Signal = await self._strategy.should_enter(
                            market, tick
                        )

                    if entry_signal.is_actionable():
                        log.info(
                            "entry_signal_detected",
                            signal_type=entry_signal.type.value,
                            confidence=entry_signal.confidence,
                            reason=entry_signal.reason,
                        )
                        await self._evaluate_risk_and_execute(
                            market, entry_signal, tick
                        )

                    SIGNALS_GENERATED.labels(
                        type=entry_signal.type.value,
                        asset=market.asset.value,
                    ).inc()

            except Exception as e:
                log.error("market_cycle_error", error=str(e))
                CYCLE_ERRORS.inc()
                cycle_span.set_attribute("error", str(e)[:256])

            finally:
                # ── 6. Fin de ciclo — SIEMPRE se ejecuta ──────────────
                await self._strategy.on_exit(market)

    # ------------------------------------------------------------------
    # ENTRADA: RIESGO + EJECUCIÓN
    # ------------------------------------------------------------------

    async def _evaluate_risk_and_execute(
        self,
        market: Market,
        entry_signal: Signal,
        tick:         MarketTick | None = None,
    ) -> None:
        """
        Evalúa riesgo de la señal y ejecuta si es aprobada.
        Si el riesgo deniega, notifica al usuario.

        Instrumentado con OpenTelemetry: sub-spans anidados para
        risk_evaluation y execution.
        """
        tracer = get_tracer()

        # Construye el contexto del portfolio para RiskEngine
        open_positions  = await self._repo.get_positions(open_only=True)
        balance         = await self._portfolio.get_balance()
        market_exposure = sum(
            p.amount for p in open_positions
            if p.market_id == market.id
        )
        total_exposure  = sum(p.amount for p in open_positions)

        # Extrae datos de mercado para RiskContext (Kelly Criterion)
        market_yes_price = tick.yes_price if tick else 0.5

        # P11.3: Liquidity-aware sizing — reduce position size in thin markets
        requested_amount = self._position_size
        if tick is not None:
            tick_data = self._build_tick_data_from_market_tick(tick)
            assessment = self._liquidity_sizer.assess(
                tick_data=tick_data,
                order_size=self._position_size,
                side="entry",
            )
            requested_amount = assessment.recommended_size

            # ── P11.3 metrics ──────────────────────────────────────
            asset_label = market.asset.value if market.asset else "UNKNOWN"
            LIQUIDITY_MULTIPLIER.labels(
                asset=asset_label, side="entry"
            ).set(assessment.liquidity_multiplier)
            LIQUIDITY_DEPTH_COVERAGE.labels(
                asset=asset_label, side="entry"
            ).set(assessment.depth_coverage)
            LIQUIDITY_SPREAD_FACTOR.labels(
                asset=asset_label, side="entry"
            ).set(assessment.spread_factor)
            LIQUIDITY_VOLUME_FACTOR.labels(
                asset=asset_label, side="entry"
            ).set(assessment.volume_factor)

            if assessment.is_reduced:
                for reason in assessment.reasons:
                    LIQUIDITY_SIZE_REDUCTIONS.labels(
                        asset=asset_label, reason=reason
                    ).inc()
                logger.info(
                    "liquidity_size_reduced",
                    market_id=market.id,
                    original=self._position_size,
                    adjusted=requested_amount,
                    multiplier=assessment.liquidity_multiplier,
                    reasons=assessment.reasons,
                )

        # ── Sub-span: risk_evaluation ─────────────────────────────────
        with tracer.start_as_current_span("risk_evaluation") as risk_span:
            risk_span.set_attribute("market.id", market.id)
            risk_span.set_attribute("signal.confidence", str(entry_signal.confidence))
            risk_span.set_attribute("requested_amount", str(requested_amount))
            risk_span.set_attribute("current_balance", str(balance))

            # Evalúa riesgo con contexto completo
            risk_decision = await self._risk.evaluate(
                signal=entry_signal,
                current_balance=balance,
                open_positions_count=len(open_positions),
                market_exposure_usdc=market_exposure,
                total_exposure_usdc=total_exposure,
                requested_amount=requested_amount,
                market_id=market.id,
                trading_mode=self._trading_mode,
                market_yes_price=market_yes_price,
            )

            risk_span.set_attribute("risk.allowed", str(risk_decision.allowed))
            risk_span.set_attribute("risk.rule", risk_decision.rule_triggered)

        if risk_decision.allowed:
            # ── Sub-span: execution ───────────────────────────────────
            with tracer.start_as_current_span("execution") as exec_span:
                exec_span.set_attribute("execution.type", "entry")
                exec_span.set_attribute("execution.market_id", market.id)

                # R2.2.1 (Ola 1.3): usa el monto ajustado por el RiskEngine
                # si lo hay. NO usar `or` — `suggested_amount == 0.0` es un
                # valor legítimo (Kelly / max_exposure pueden sugerir "no
                # arriesgues más"). `is not None` respeta ese caso.
                amount = (
                    risk_decision.suggested_amount
                    if risk_decision.suggested_amount is not None
                    else requested_amount
                )

                result = await self._execution.execute_entry(
                    signal=entry_signal,
                    market_id=market.id,
                    amount=amount,
                )

                exec_span.set_attribute("execution.success", str(result.success))
                if result.fill_price:
                    exec_span.set_attribute("execution.fill_price", str(result.fill_price))

            if result.success:
                self._strategy.mark_entry(
                    strategy_name=entry_signal.source_strategy,
                    market_id=market.id,
                    price=result.fill_price,
                )
                logger.info(
                    "entry_executed",
                    market_id=market.id,
                    strategy=entry_signal.source_strategy,
                    fill_price=result.fill_price,
                    amount=amount,
                )
        else:
            # Notifica al usuario si el riesgo bloqueó la operación
            logger.info(
                "risk_denied",
                market_id=market.id,
                rule=risk_decision.rule_triggered,
                reason=risk_decision.reason,
            )
            await self._notifier.send_risk_alert(
                rule_triggered=risk_decision.rule_triggered,
                reason=risk_decision.reason,
            )

    # ------------------------------------------------------------------
    # SALIDA
    # ------------------------------------------------------------------

    async def _execute_exit(
        self,
        market: Market,
        exit_signal: Signal,
    ) -> None:
        """
        Ejecuta la salida de una posición existente.
        Busca la posición abierta para este mercado y la cierra.

        Instrumentado con OpenTelemetry: sub-span para exit execution.
        """
        tracer = get_tracer()

        # Obtiene todas las posiciones abiertas y filtra por market_id
        all_open = await self._repo.get_positions(open_only=True)
        market_positions = [p for p in all_open if p.market_id == market.id]

        if not market_positions:
            logger.warning(
                "exit_signal_no_open_position",
                market_id=market.id,
            )
            return

        position = market_positions[0]

        with tracer.start_as_current_span("execution") as exec_span:
            exec_span.set_attribute("execution.type", "exit")
            exec_span.set_attribute("execution.market_id", market.id)
            exec_span.set_attribute("execution.exit_reason", exit_signal.reason)

            result = await self._execution.execute_exit(
                position=position,
                reason=exit_signal.reason,
            )

            exec_span.set_attribute("execution.success", str(result.success))
            if result.pnl is not None:
                exec_span.set_attribute("execution.pnl", str(result.pnl))

        if result.success:
            self._strategy.mark_exit(
                strategy_name=exit_signal.source_strategy,
                market_id=market.id,
            )
            logger.info(
                "exit_executed",
                market_id=market.id,
                pnl=result.pnl,
                reason=exit_signal.reason,
            )

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    async def _get_current_tick(
        self, market: Market
    ) -> MarketTick | None:
        """
        Obtiene el tick más reciente del mercado vía MarketService.
        Delega en el market data port (HTTP client con fallback a WS).
        """
        try:
            return await self._market_svc.get_market_tick(market.id)
        except Exception as e:
            logger.debug(
                "tick_fetch_failed",
                market_id=market.id,
                error=str(e),
            )
            return None

    @staticmethod
    def _build_tick_data_from_market_tick(tick: MarketTick) -> dict:
        """Build a FillSimulator-compatible tick dict from a MarketTick.

        Used by P11.3 LiquidityAwareSizer to assess market depth before
        submitting orders. Uses best-effort depth estimation from
        spread and volume when real orderbook data is unavailable.
        """
        spread = tick.spread if tick.spread > 0 else 0.01
        mid = tick.yes_price
        best_bid = (
            tick.best_bid
            if hasattr(tick, 'best_bid') and tick.best_bid > 0
            else mid - spread / 2
        )
        best_ask = (
            tick.best_ask
            if hasattr(tick, 'best_ask') and tick.best_ask > 0
            else mid + spread / 2
        )

        # Estimate depth from volume and spread: deeper markets have more liquidity
        vol = tick.volume_24h if tick.volume_24h > 0 else 0.0
        # Default depth: at least $50 or 5× position size as safe floor
        # This prevents overly aggressive size reduction when real orderbook
        # depth data is unavailable (MarketTick lacks bids_vol/asks_vol).
        estimated_l1 = max(50.0, vol / 1440.0) if vol > 0 else 50.0

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "bids_vol_1": estimated_l1,
            "bids_vol_2": 0.0,
            "bids_vol_3": 0.0,
            "asks_vol_1": estimated_l1,
            "asks_vol_2": 0.0,
            "asks_vol_3": 0.0,
            "volume_24h": vol,
        }
