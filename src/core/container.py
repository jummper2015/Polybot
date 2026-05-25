# src/core/container.py

import time

import structlog
from redis.asyncio import Redis as AsyncRedis

from src.application.services.market_service import MarketService
from src.application.services.portfolio_service import PortfolioService
from src.application.services.trading_service import TradingService
from src.domain.enums.asset import Asset
from src.domain.enums.window import Window
from src.execution.paper_handler import PaperTradingHandler
from src.execution.real_handler import RealTradingHandler
from src.infrastructure.cache.redis_client import RedisClient
from src.infrastructure.db.repository import SQLAlchemyRepository
from src.infrastructure.db.session import create_engine, create_session_factory
# ServiceStatusEnum imported locally in health_check_all() to avoid
# circular import with API schemas loaded at FastAPI startup.
from src.infrastructure.polymarket.http_client import PolymarketHTTPClient
from src.infrastructure.polymarket.ws_client import PolymarketWSClient
from src.infrastructure.security.audit_log import AuditLogger
from src.infrastructure.security.circuit_breaker import (
    CircuitBreakerConfig,
    CLOBCircuitBreaker,
)
from src.infrastructure.security.key_manager import KeyManager
from src.infrastructure.security.rate_limiter import RateLimiter
from src.infrastructure.security.secure_config import SecureConfig
from src.infrastructure.security.security_guard import SecurityGuard
from src.interfaces.telegram.bot import create_bot, create_dispatcher
from src.interfaces.telegram.handlers.alerts import TelegramNotifier
from src.risk.engine import RiskEngine, RiskEngineConfig
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.engine import StrategyEngine
from src.strategies.filters.multi_timeframe import MultiTimeframeFilter
from src.strategies.mean_reversion.config import MeanReversionConfig
from src.strategies.mean_reversion.strategy import MeanReversionStrategy

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

        # Modo de trading en runtime (puede cambiar vía Telegram)
        # SecureConfig es frozen, así que guardamos el modo por separado
        self._runtime_mode = config.trading_mode

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

    @property
    def trading_mode(self) -> str:
        """Modo de trading actual: 'paper' o 'real'."""
        return self._runtime_mode

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
        mr_config = MeanReversionConfig(
            ma_window          = getattr(self.config, 'mr_ma_window', 20),
            entry_zscore       = getattr(self.config, 'mr_entry_zscore', -2.0),
            exit_zscore        = getattr(self.config, 'mr_exit_zscore', 0.0),
            stop_loss_pct      = getattr(self.config, 'mr_stop_loss_pct', 0.10),
            timeout_minutes    = getattr(self.config, 'mr_timeout_minutes', 45.0),
            max_spread         = getattr(self.config, 'mr_max_spread', 0.03),
            min_volume_usdc    = getattr(self.config, 'mr_min_volume_usdc', 1000.0),
            position_size_usdc = getattr(self.config, 'mr_position_size_usdc', 10.0),
        )
        self.strategy_engine = StrategyEngine(
            strategies=[
                BuyAboveThresholdStrategy(config=bat_config),
                MeanReversionStrategy(config=mr_config),
            ]
        )

        # ── 6. Risk Engine ────────────────────────────────────────────
        log.info("init_step", step="risk_engine")
        risk_config = RiskEngineConfig(
            min_balance_usdc       = self.config.risk_min_balance_usdc,
            max_daily_drawdown_pct = self.config.risk_max_drawdown_pct,
            max_exposure_pct       = self.config.risk_max_exposure_pct,
            max_open_positions     = self.config.risk_max_positions,
            kelly_cap              = getattr(self.config, 'kelly_cap', 0.25),
            kelly_safety_factor    = getattr(self.config, 'kelly_safety_factor', 0.25),
            kelly_target_price     = self.config.bat_target_price,
            kelly_position_floor   = getattr(self.config, 'kelly_position_floor', 5.0),
            kelly_position_cap     = getattr(self.config, 'kelly_position_cap', 50.0),
        )
        self.risk_engine = RiskEngine(config=risk_config)

        # ── 7. Telegram Bot (necesario antes de notifier) ─────────────
        log.info("init_step", step="telegram")
        import os
        try:
            self.telegram_bot = create_bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
            self.telegram_dp  = create_dispatcher(
                redis=self.redis_raw,
                container=self,  # Inyecta el container en los handlers vía middleware
            )
            self.notifier     = TelegramNotifier(
                bot=self.telegram_bot,
                chat_id=self.config.telegram_chat_id,
            )
            log.info("telegram_initialized")
        except Exception as e:
            log.warning("telegram_init_skipped", reason=str(e))
            self.telegram_bot = None
            self.telegram_dp  = None
            self.notifier     = None

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
            circuit_breaker = CLOBCircuitBreaker(
                config=CircuitBreakerConfig(
                    failure_threshold=5,
                    recovery_timeout=60.0,
                    window_seconds=60.0,
                )
            )
            self.execution_handler = RealTradingHandler(
                clob_client=clob,
                repository=self.repository,
                redis=self.redis,
                notifier=self.notifier,
                audit_logger=self.audit_logger,
                security_guard=self.security_guard,
                circuit_breaker=circuit_breaker,
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
            portfolio_service=self.portfolio_service,
            position_size_usdc=self.config.bat_position_size_usdc,
            trading_mode=self.config.trading_mode,
        )

        # ── 9.5 Multi-Timeframe Filter ────────────────────────────────
        # Inyecta el filtro en la estrategia BAT después de que
        # MarketService esté disponible (necesario para obtener tick M15)
        log.info("init_step", step="mtf_filter")
        await self._wire_mtf_filter()

        log.info("container_initialized", mode=self.config.trading_mode)

    async def _wire_mtf_filter(self) -> None:
        """
        Crea e inyecta el filtro Multi-Timeframe en la estrategia BAT.

        El filtro consulta el tick M15 del mismo asset para confirmar
        señales generadas en M5. Reduce falsos positivos ~40%.
        """
        # Construye el tick_provider: dado un Asset, obtiene el tick M15
        async def _get_m15_tick(asset: Asset):
            try:
                markets = await self.market_service.get_active_markets(
                    asset=asset.value,
                    window=Window.M15.value,
                )
                if not markets:
                    return None
                m15_market = markets[0]
                return await self.market_service.get_market_tick(m15_market.id)
            except Exception:
                return None

        # Crea el filtro con el threshold de BAT
        mtf = MultiTimeframeFilter(
            tick_provider=_get_m15_tick,
            threshold=self.config.bat_threshold,
        )

        # Inyecta en la estrategia BAT (siempre es la primera)
        from src.strategies.buy_above_threshold.strategy import (
            BuyAboveThresholdStrategy,
        )
        for strategy in self.strategy_engine._strategies:
            if isinstance(strategy, BuyAboveThresholdStrategy):
                strategy.set_mtf_filter(mtf)
                break

        logger.info("mtf_filter_wired", threshold=self.config.bat_threshold)

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
        import httpx

        from src.interfaces.api.schemas.health_schema import ServiceStatusEnum

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
        if self.telegram_bot is None:
            results["telegram"] = ServiceStatusEnum.DEGRADED
        else:
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

    # ------------------------------------------------------------------
    # MÉTODOS PARA TELEGRAM HANDLERS
    # ------------------------------------------------------------------

    async def get_bot_status(self) -> dict:
        """
        Devuelve el estado real del bot para el comando /status.
        Usa el container para obtener datos reales de todos los servicios.
        """
        try:
            markets = await self.market_service.get_active_markets()
        except Exception:
            markets = []

        try:
            open_positions = await self.repository.get_positions(open_only=True)
        except Exception:
            open_positions = []

        try:
            balance = await self.portfolio_service.get_balance()
        except Exception:
            balance = 0.0

        try:
            total_pnl = await self.repository.get_total_pnl(
                mode=self._runtime_mode
            )
        except Exception:
            total_pnl = 0.0

        is_running = (
            self.trading_service is not None
            and self.trading_service._running
        )

        return {
            "running": is_running,
            "mode": self._runtime_mode,
            "active_markets": len(markets),
            "open_positions": len(open_positions),
            "balance": round(balance, 2),
            "pnl": round(total_pnl, 4),
            "uptime_seconds": self.uptime_seconds(),
        }

    async def update_bat_setting(
        self, key: str, value: float
    ) -> tuple[bool, str]:
        """
        Actualiza un parámetro de la estrategia BAT en caliente.
        Valida el valor y persiste en bot_settings para sobrevivir reinicios.

        Retorna (success, message).
        """
        try:
            from src.strategies.buy_above_threshold.strategy import (
                BuyAboveThresholdStrategy,
            )

            # Encuentra la estrategia BAT en el engine
            bat = None
            for s in self.strategy_engine._strategies:
                if isinstance(s, BuyAboveThresholdStrategy):
                    bat = s
                    break

            if bat is None:
                return False, "Estrategia BAT no encontrada"

            current = bat._config
            updates = {}

            if key == "threshold":
                if not 0.50 <= value <= 0.95:
                    return False, f"Threshold debe estar entre 0.50 y 0.95"
                updates["threshold"] = value
                if value >= current.target_price:
                    updates["target_price"] = min(value + 0.05, 0.99)
                if value <= current.stop_drop_floor:
                    updates["stop_drop_floor"] = value - 0.10

            elif key == "stop_loss":
                if not 0.05 <= value <= 0.50:
                    return False, f"Stop loss debe estar entre 5% y 50%"
                updates["stop_loss_pct"] = value

            elif key == "target_price":
                if not 0.76 <= value <= 0.99:
                    return False, f"Target price debe estar entre 0.76 y 0.99"
                if value <= current.threshold:
                    return False, f"Target price debe ser > threshold ({current.threshold})"
                updates["target_price"] = value

            elif key == "position_size":
                if not 1.0 <= value <= 500.0:
                    return False, f"Position size debe estar entre 1 y 500 USDC"
                updates["position_size_usdc"] = value
                # También actualiza el trading service
                self.trading_service._position_size = value

            elif key == "required_ticks":
                if not 1 <= value <= 20:
                    return False, f"Required ticks debe estar entre 1 y 20"
                updates["required_ticks"] = int(value)

            else:
                return False, f"Parámetro desconocido: {key}"

            if not updates:
                return False, "No hay cambios que aplicar"

            # Crea nueva config con los updates
            from src.strategies.buy_above_threshold.config import (
                BuyAboveThresholdConfig,
            )
            new_config_dict = {
                "threshold": current.threshold,
                "required_ticks": current.required_ticks,
                "max_spread": current.max_spread,
                "min_volume_usdc": current.min_volume_usdc,
                "blocked_hours": current.blocked_hours,
                "stop_loss_pct": current.stop_loss_pct,
                "stop_drop_floor": current.stop_drop_floor,
                "timeout_minutes": current.timeout_minutes,
                "target_price": current.target_price,
                "hedge_drop_pct": current.hedge_drop_pct,
                "hedge_enabled": current.hedge_enabled,
                "position_size_usdc": current.position_size_usdc,
            }
            new_config_dict.update(updates)
            new_config = BuyAboveThresholdConfig(**new_config_dict)

            # Aplica en caliente
            bat.update_config(new_config)

            # Persiste en DB para sobrevivir reinicios
            for k, v in updates.items():
                await self.repository.set_bot_setting(
                    f"bat_{k}", str(v)
                )

            # Si cambió el threshold, actualizar también el filtro MTF
            if "threshold" in updates:
                await self._wire_mtf_filter()

            logger.info(
                "bat_setting_updated",
                key=key,
                value=str(value),
                updates=str(updates),
            )

            return True, f"{key} actualizado a {value}"

        except ValueError as e:
            return False, f"Valor inválido: {e}"
        except Exception as e:
            logger.error("update_bat_setting_error", error=str(e))
            return False, f"Error interno: {e}"

    async def enable_real_mode(self) -> tuple[bool, str]:
        """
        Activa el modo de trading REAL después de doble confirmación Telegram.
        Detiene el trading service, recrea el handler real y rearranca.
        """
        if self.config.trading_mode == "real":
            return False, "El bot ya está en modo REAL"

        try:
            was_running = (
                self.trading_service is not None
                and self.trading_service._running
            )

            # 1. Detener ciclos de trading
            if self.trading_service:
                await self.trading_service.stop()

            # 2. Crear handler real (requiere KeyManager)
            if self.key_manager is None:
                self.key_manager = KeyManager()

            from src.infrastructure.polymarket.clob_client import (
                PolymarketCLOBClient,
            )
            clob = PolymarketCLOBClient(key_manager=self.key_manager)

            circuit_breaker = CLOBCircuitBreaker(
                config=CircuitBreakerConfig(
                    failure_threshold=5,
                    recovery_timeout=60.0,
                    window_seconds=60.0,
                )
            )

            # 3. Crear nuevo RealTradingHandler
            self.execution_handler = RealTradingHandler(
                clob_client=clob,
                repository=self.repository,
                redis=self.redis,
                notifier=self.notifier,
                audit_logger=self.audit_logger,
                security_guard=self.security_guard,
                circuit_breaker=circuit_breaker,
            )

            # 4. Recrear PortfolioService con el handler real
            self.portfolio_service = PortfolioService(
                repository=self.repository,
                paper_handler=None,  # Ya no es paper
                redis=self.redis,
            )

            # 5. Recrear TradingService en modo real
            self.trading_service = TradingService(
                market_service=self.market_service,
                strategy_engine=self.strategy_engine,
                risk_engine=self.risk_engine,
                execution_handler=self.execution_handler,
                repository=self.repository,
                notifier=self.notifier,
                portfolio_service=self.portfolio_service,
                position_size_usdc=self.config.bat_position_size_usdc,
                trading_mode="real",
            )

            # 6. Actualizar modo de trading en runtime
            self._runtime_mode = "real"

            # 7. Persistir modo en DB
            await self.repository.set_bot_setting("trading_mode", "real")

            # 8. Rearrancar si estaba corriendo
            if was_running:
                await self.trading_service.start()

            logger.info(
                "real_mode_activated",
                was_running=was_running,
            )

            return True, "Modo REAL activado correctamente"

        except Exception as e:
            logger.error("enable_real_mode_error", error=str(e))
            return False, f"Error al activar modo REAL: {e}"

    async def start_bot(self) -> tuple[bool, str]:
        """Arranca el bot de trading desde Telegram."""
        try:
            if self.trading_service._running:
                return False, "El bot ya está corriendo"
            await self.trading_service.start()
            return True, "Bot iniciado correctamente"
        except Exception as e:
            logger.error("start_bot_error", error=str(e))
            return False, f"Error al iniciar: {e}"

    async def stop_bot(self) -> tuple[bool, str]:
        """Detiene el bot de trading desde Telegram."""
        try:
            if not self.trading_service._running:
                return False, "El bot ya está detenido"
            await self.trading_service.stop()
            return True, "Bot detenido correctamente"
        except Exception as e:
            logger.error("stop_bot_error", error=str(e))
            return False, f"Error al detener: {e}"
