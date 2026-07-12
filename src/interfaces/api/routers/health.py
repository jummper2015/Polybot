# src/interfaces/api/routers/health.py

import httpx
import structlog
from fastapi import APIRouter, Request

from src.infrastructure.observability.metrics import BOT_UPTIME
from src.interfaces.api.schemas.health_schema import HealthResponse, ServiceStatusEnum

logger = structlog.get_logger(__name__)
router = APIRouter()


async def _check_database(container) -> ServiceStatusEnum:
    """Verifica conectividad con PostgreSQL ejecutando una query simple."""
    try:
        await container.repository.get_active_markets()
        return ServiceStatusEnum.OK
    except Exception as e:
        logger.warning("health_db_failed", error=str(e))
        return ServiceStatusEnum.DOWN


async def _check_redis(container) -> ServiceStatusEnum:
    """Verifica conectividad con Redis via ping."""
    try:
        await container.redis._redis.ping()
        return ServiceStatusEnum.OK
    except Exception as e:
        logger.warning("health_redis_failed", error=str(e))
        return ServiceStatusEnum.DOWN


async def _check_polymarket(container) -> ServiceStatusEnum:
    """
    Verifica que la API de Polymarket responde.
    Timeout corto — no queremos bloquear el health check.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                "https://gamma-api.polymarket.com/markets",
                params={"_limit": "1"},
            )
            if response.status_code == 200:
                return ServiceStatusEnum.OK
            return ServiceStatusEnum.DEGRADED
    except Exception as e:
        logger.warning("health_polymarket_failed", error=str(e))
        return ServiceStatusEnum.DOWN


async def _check_telegram(container) -> ServiceStatusEnum:
    """Verifica que el bot de Telegram está conectado."""
    try:
        bot = container.telegram_bot
        if bot:
            me = await bot.get_me()
            if me.id:
                return ServiceStatusEnum.OK
        return ServiceStatusEnum.DEGRADED
    except Exception as e:
        logger.warning("health_telegram_failed", error=str(e))
        return ServiceStatusEnum.DOWN


async def _check_data_api_cross_verify(container) -> ServiceStatusEnum:
    """
    Verifica que las posiciones locales coincidan con la Data API de Polymarket.

    Compara posiciones abiertas en DB local vs GET /positions de data-api.
    Solo aplica en modo real (en paper: OK).

    Estados:
      - OK: sin discrepancias o modo paper
      - DEGRADED: discrepancias menores (size mismatch ≤ 1)
      - DOWN: discrepancias mayores (missing positions, múltiples mismatches)
    """
    try:
        result = await container.cross_verify_positions()

        status = result.get("status", "down")

        if status == "ok" or status == "skipped":
            return ServiceStatusEnum.OK
        elif status == "degraded":
            logger.warning(
                "health_data_api_degraded",
                discrepancies=result.get("discrepancies", []),
                error=result.get("error"),
            )
            return ServiceStatusEnum.DEGRADED
        else:
            logger.warning(
                "health_data_api_down",
                reason=result.get("reason"),
                error=result.get("error"),
                discrepancies=len(result.get("discrepancies", [])),
            )
            return ServiceStatusEnum.DOWN

    except Exception as e:
        logger.warning("health_data_api_failed", error=str(e))
        return ServiceStatusEnum.DEGRADED


async def _check_websockets(container) -> ServiceStatusEnum:
    """
    Verifica estado de las conexiones WS activas.
    OK si al menos una conexión está activa, DEGRADED si ninguna.
    """
    try:
        ws_client = container.ws_client
        if not ws_client._subscriptions:
            return ServiceStatusEnum.DEGRADED  # Sin subscripciones activas

        active = sum(
            1 for task in ws_client._subscriptions.values()
            if not task.done()
        )
        return ServiceStatusEnum.OK if active > 0 else ServiceStatusEnum.DEGRADED

    except Exception as e:
        logger.warning("health_ws_failed", error=str(e))
        return ServiceStatusEnum.DOWN


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check completo del sistema",
)
async def health_check(request: Request) -> HealthResponse:
    """
    Verifica el estado de todos los servicios del sistema.
    Responde en < 5s — usa timeouts cortos internamente.
    Usado por Docker healthcheck y load balancers.
    """
    container = request.app.state.container

    # Ejecuta todos los checks en paralelo para minimizar latencia
    import asyncio
    results = await asyncio.gather(
        _check_database(container),
        _check_redis(container),
        _check_polymarket(container),
        _check_telegram(container),
        _check_websockets(container),
        _check_data_api_cross_verify(container),
        return_exceptions=True,
    )

    # Mapea resultados (maneja excepciones inesperadas)
    services: dict[str, ServiceStatusEnum] = {
        "database":       results[0] if not isinstance(results[0], BaseException)  # type: ignore[arg-type]
                          else ServiceStatusEnum.DOWN,
        "redis":          results[1] if not isinstance(results[1], BaseException)  # type: ignore[arg-type]
                          else ServiceStatusEnum.DOWN,
        "polymarket":     results[2] if not isinstance(results[2], BaseException)  # type: ignore[arg-type]
                          else ServiceStatusEnum.DOWN,
        "telegram":       results[3] if not isinstance(results[3], BaseException)  # type: ignore[arg-type]
                          else ServiceStatusEnum.DOWN,
        "websockets":     results[4] if not isinstance(results[4], BaseException)  # type: ignore[arg-type]
                          else ServiceStatusEnum.DOWN,
        "data_api_cross": results[5] if not isinstance(results[5], BaseException)  # type: ignore[arg-type]
                          else ServiceStatusEnum.DEGRADED,
    }

    # Estado global: OK solo si todos OK
    # DEGRADED si alguno DEGRADED, DOWN si alguno DOWN
    if any(v == ServiceStatusEnum.DOWN for v in services.values()):
        overall = ServiceStatusEnum.DOWN
    elif any(v == ServiceStatusEnum.DEGRADED for v in services.values()):
        overall = ServiceStatusEnum.DEGRADED
    else:
        overall = ServiceStatusEnum.OK

    # Actualiza métrica de uptime
    BOT_UPTIME.set(container.uptime_seconds())

    log = logger.bind(overall=overall.value, services={k: v.value for k, v in services.items()})
    if overall == ServiceStatusEnum.OK:
        log.info("health_check_ok")
    else:
        log.warning("health_check_degraded")

    return HealthResponse(
        status=overall,
        version="1.0.0",
        mode=container.config.trading_mode,
        services=services,
    )
