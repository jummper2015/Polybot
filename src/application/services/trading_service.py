# src/application/services/trading_service.py

import asyncio
import structlog
from datetime import datetime

from src.domain.entities.market import Market
from src.application.services.market_service import MarketService
from src.application.ports.repository_port import IRepositoryPort
from src.strategies.engine import StrategyEngine
from src.risk.engine import RiskEngine
from src.execution.base import IExecutionHandler
from src.infrastructure.observability.metrics import (
    CYCLE_DURATION, CYCLE_ERRORS, SIGNALS_GENERATED
)

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
        market_service:   MarketService,
        strategy_engine:  StrategyEngine,
        risk_engine:      RiskEngine,
        execution_handler: IExecutionHandler,
        repository:       IRepositoryPort,
    ):
        self._market_svc   = market_service
        self._strategy     = strategy_engine
        self._risk         = risk_engine
        self._execution    = execution_handler
        self._repo         = repository

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

    # Actualización de src/application/services/trading_service.py
# Reemplaza el bloque de evaluación de riesgo en _run_market_cycle()

async def _run_market_cycle(self, market: Market) -> None:

    if entry_signal.is_actionable():

        # Construye los datos del contexto consultando el portfolio
        open_positions  = await self._repo.get_positions(open_only=True)
        balance         = await self._portfolio.get_balance()
        market_exposure = sum(
            p.amount for p in open_positions
            if p.market_id == market.id
        )
        total_exposure  = sum(p.amount for p in open_positions)

        # Evalúa riesgo con contexto completo
        risk_decision = await self._risk.evaluate(
            signal=entry_signal,
            current_balance=balance,
            open_positions_count=len(open_positions),
            market_exposure_usdc=market_exposure,
            total_exposure_usdc=total_exposure,
            requested_amount=self._config.position_size_usdc,
            market_id=market.id,
            trading_mode=self._config.trading_mode,
        )

        if risk_decision.allowed:
            # Usa el monto ajustado por el RiskEngine si lo hay
            amount = risk_decision.suggested_amount or self._config.position_size_usdc

            result = await self._execution.execute_entry(
                signal=entry_signal,
                market_id=market.id,
                amount=amount,
            )

            if result.success:
                self._strategy.mark_entry(
                    strategy_name=entry_signal.source_strategy,
                    market_id=market.id,
                    price=result.fill_price,
                )
        else:
            # Notifica al usuario si el riesgo bloqueó la operación
            await self._notifier.send_risk_alert(
                rule_triggered=risk_decision.rule_triggered,
                reason=risk_decision.reason,
            )