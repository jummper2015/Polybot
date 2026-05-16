# src/core/container.py

import time
import structlog
from datetime import datetime, timezone
from redis.asyncio import Redis as AsyncRedis

from src.infrastructure.security.secure_config import SecureConfig
from src.infrastructure.security.key_manager import KeyManager
from src.infrastructure.security.audit_log import AuditLogger
from src.infrastructure.security.rate_limiter import RateLimiter
from src.infrastructure.security.security_guard import SecurityGuard
from src.infrastructure.db.session import create_engine, create_session_factory
from src.infrastructure.db.repository import SQLAlchemyRepository
from src.infrastructure.cache.redis_client import RedisClient
from src.infrastructure.polymarket.ws_client import PolymarketWSClient
from src.infrastructure.polymarket.http_client import PolymarketHTTPClient
from src.infrastructure.observability.metrics import (
    ServiceStatusEnum,
    BOT_UPTIME,
)
from src.strategies.engine import StrategyEngine
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.risk.engine import RiskEngine, RiskEngineConfig
from src.execution.paper_handler import PaperTradingHandler
from src.execution.real_handler import RealTradingHandler
from src.application.services.market_service import MarketService
from src.application.services.portfolio_service import PortfolioService
from src.application.services.trading_service import TradingService
from src.interfaces.telegram.handlers.alerts import TelegramNotifier
from src.interfaces.telegram.bot import create_bot, create_dispatcher

logger = structlog.get_logger(__name__)


class Container:
    """
    DI Container: construye y conecta todos los componentes del sistema.
    Orden de inicialización determinista — cada componente
    solo se crea cuando sus dependencias están listas.

    Orden: Config → DB → Redis → Security → Polymarket →
           Strategy → Risk → Execution → Notification →
           Services → Telegram → (FastAPI via lifespan)
    """

    def __init__(self, config: SecureConfig):
        self.config    = config
        self._started_at = time.monotonic()

        # Todos los componentes se inicializan en None
        # y se asignan en init() en orden
        self.engine            = None
        self.session_factory   = None
        self.repository        = None
        self.redis_raw         = None
        self.redis             = None
        self.key_manager       = None
        self.audit_logger      = None
        self.rate_limiter      = None
        self.security_guard    = None
        self.ws_client         = None
        self.http_client       = None
        self.strategy_engine   = None
        self.risk_engine       = None
        self.execution_handler = None
        self.telegram_bot      = None
        self.telegram_dp       = None
        self.notifier          = None
        self.market_service    = None
        self.portfolio_service = None
        self.trading_service   = None

    async def init(self) -> None:
        """
        Inicializa todos los componentes en orden.
        Cualquier fallo en este método detiene el arranque.
        """
        log = logger.bind(action="container_init")
        log.info("container_initializing")

        # ── 1. Base de Datos ──────────────────────────────────────────
        log.info("init_step", step="database")
        self.engine          = create_engine(self.config.database_url)
        self.session_factory = create_session_factory(self.engine)
        self.repository      = SQLAlchemyRepository(self.session_factory)

        # ── 2. Redis ──────────────────────────────────────────────────
        log.info("init_step", step="redis")
        self.redis_raw = AsyncRedis.from_url(
            self.config.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        self.redis = RedisClient(self.redis_raw)

        # ── 3. Seguridad ──────────────────────────────────────────────
        log.info("init_step", step="security")
        self.key_manager  = (
            KeyManager() if self.config.trading_mode == "real" else None
        )
        self.audit_logger  = AuditLogger(repository=self.repository)
        self.rate_limiter  = RateLimiter(redis=self.redis_raw)
        self.security_guard = SecurityGuard(
            config=self.config,
            key_manager=self.key_manager,
            rate_limiter=self.rate_limiter,
            audit_logger=self.audit_logger,
        )

        # ── 4. Polymarket API (HTTP + WS) ─────────────────────────────
        log.info("init_step", step="polymarket")
        self.ws_client   = PolymarketWSClient(redis=self.redis)
        self.http_client = PolymarketHTTPClient(ws_client=self.ws_client)

        # ── 5. Strategy Engine ────────────────────────────────────────
        log.info("init_step", step="strategy_engine")
        bat_config = BuyAboveThresholdConfig(
            threshold          = self.config.bat_threshold,
            required_ticks     = self.config.bat_required_ticks,
            stop_loss_pct      = self.config.bat_stop_loss_pct,
            target_price       = self.config.bat_target_price,
            position_size_usdc = self.config.bat_position_size_usdc,
        )
        self.strategy_engine = StrategyEngine(
            strategies=[BuyAboveThresholdStrategy(config=bat_config)]
        )

        # ── 6. Risk Engine ────────────────────────────────────────────
        log.info("init_step", step="risk_engine")
        risk_config = RiskEngineConfig(
            min_balance_usdc       = self.config.risk_min_balance_usdc,
            max_daily_drawdown_pct = self.config.risk_max_drawdown_pct,
            max_exposure_pct       = self.config.risk_max_exposure_pct,
            max_open_positions     = self.config.risk_max_positions,
        )
        self.risk_engine = RiskEngine(config=risk_config)

        # ── 7. Telegram Bot (necesario antes de notifier) ─────────────
        log.info("init_step", step="telegram")
        import os
        self.telegram_bot = create_bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
        self.telegram_dp  = create_dispatcher(redis=self.redis_raw)
        self.notifier     = TelegramNotifier(
            bot=self.telegram_bot,
            chat_id=self.config.telegram_chat_id,
        )

        # ── 8. Execution Handler ──────────────────────────────────────
        log.info("init_step", step="execution_handler")
        if self.config.trading_mode == "paper":
            self.execution_handler = PaperTradingHandler(
                repository=self.repository,
                redis=self.redis,
                notifier=self.notifier,
                initial_balance=self.config.paper_initial_balance,
            )
        else:
            from src.infrastructure.polymarket.clob_client import PolymarketCLOBClient
            clob = PolymarketCLOBClient(key_manager=self.key_manager)
            self.execution_handler = RealTradingHandler(
                clob_client=clob,
                repository=self.repository,
                redis=self.redis,
                notifier=self.notifier,
                audit_logger=self.audit_logger,
                security_guard=self.security_guard,
            )

        # ── 9. Application Services ───────────────────────────────────
        log.info("init_step", step="application_services")
        self.market_service = MarketService(
            market_data_port=self.http_client,
            repository=self.repository,
            redis=self.redis,
        )
        self.portfolio_service = PortfolioService(
            repository=self.repository,
            paper_handler=(
                self.execution_handler
                if self.config.trading_mode == "paper"
                else None
            ),
            redis=self.redis,
        )
        self.trading_service = TradingService(
            market_service=self.market_service,
            strategy_engine=self.strategy_engine,
            risk_engine=self.risk_engine,
            execution_handler=self.execution_handler,
            repository=self.repository,
            notifier=self.notifier,
        )

        log.info("container_initialized", mode=self.config.trading_mode)

    async def shutdown(self) -> None:
        """
        Apagado limpio en orden inverso al arranque.
        Cada componente se cierra limpiamente antes del siguiente.
        """
        log = logger.bind(action="container_shutdown")
        log.info("container_shutting_down")

        # 1. Detiene el bot de trading
        if self.trading_service:
            try:
                await self.trading_service.stop()
            except Exception as e:
                log.error("shutdown_trading_service_error", error=str(e))

        # 2. Cierra conexiones WebSocket
        if self.ws_client:
            try:
                await self.ws_client.unsubscribe_all()
            except Exception as e:
                log.error("shutdown_ws_error", error=str(e))

        # 3. Cierra HTTP client
        if self.http_client:
            try:
                await self.http_client.close()
            except Exception as e:
                log.error("shutdown_http_error", error=str(e))

        # 4. Cierra Telegram bot
        if self.telegram_bot:
            try:
                await self.telegram_bot.session.close()
            except Exception as e:
                log.error("shutdown_telegram_error", error=str(e))

        # 5. Cierra Redis
        if self.redis_raw:
            try:
                await self.redis_raw.aclose()
            except Exception as e:
                log.error("shutdown_redis_error", error=str(e))

        # 6. Cierra DB engine
        if self.engine:
            try:
                await self.engine.dispose()
            except Exception as e:
                log.error("shutdown_db_error", error=str(e))

        log.info("container_shutdown_complete")

    async def health_check_all(self) -> dict[str, "ServiceStatusEnum"]:
        """
        Verifica el estado de todos los servicios.
        Usado por el endpoint /health.
        """
        from src.interfaces.api.schemas.health_schema import ServiceStatusEnum
        import httpx

        results = {}

        # DB
        try:
            await self.repository.get_active_markets()
            results["database"] = ServiceStatusEnum.OK
        except Exception:
            results["database"] = ServiceStatusEnum.DOWN

        # Redis
        try:
            await self.redis_raw.ping()
            results["redis"] = ServiceStatusEnum.OK
        except Exception:
            results["redis"] = ServiceStatusEnum.DOWN

        # Polymarket
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"_limit": "1"},
                )
                results["polymarket"] = (
                    ServiceStatusEnum.OK
                    if r.status_code == 200
                    else ServiceStatusEnum.DEGRADED
                )
        except Exception:
            results["polymarket"] = ServiceStatusEnum.DOWN

        # Telegram
        try:
            me = await self.telegram_bot.get_me()
            results["telegram"] = (
                ServiceStatusEnum.OK if me.id else ServiceStatusEnum.DEGRADED
            )
        except Exception:
            results["telegram"] = ServiceStatusEnum.DOWN

        # WebSocket
        if self.ws_client and self.ws_client._subscriptions:
            active = sum(
                1 for t in self.ws_client._subscriptions.values()
                if not t.done()
            )
            results["websockets"] = (
                ServiceStatusEnum.OK if active > 0
                else ServiceStatusEnum.DEGRADED
            )
        else:
            results["websockets"] = ServiceStatusEnum.DEGRADED

        return results

    def uptime_seconds(self) -> float:
        """Segundos desde que el container fue inicializado."""
        return time.monotonic() - self._started_at