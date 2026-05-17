# src/interfaces/api/middleware.py

import time
import uuid
import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.infrastructure.observability.metrics import (
    API_REQUEST_COUNT,
    API_REQUEST_DURATION,
)

logger = structlog.get_logger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    Middleware que añade:
    1. `request_id` único a cada request (visible en logs y headers)
    2. Logging estructurado de entrada y salida de cada request
    3. Métricas Prometheus de latencia y conteo por endpoint
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Genera ID único para trazabilidad de este request
        request_id = str(uuid.uuid4())[:8]
        start_time = time.perf_counter()

        # Bind del request_id al contexto de structlog
        # Todos los logs dentro de este request tendrán el mismo ID
        log = logger.bind(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        log.info("request_started")

        try:
            response = await call_next(request)
            status   = response.status_code

        except Exception as e:
            log.error("request_failed", error=str(e))
            status = 500
            raise

        finally:
            duration = time.perf_counter() - start_time
            endpoint = request.url.path

            # Añade request_id al header de respuesta (útil para debugging)
            if "response" in dir():
                response.headers["X-Request-ID"] = request_id

            log.info(
                "request_completed",
                status=status,
                duration_ms=round(duration * 1000, 2),
            )

            # Registra métricas Prometheus
            API_REQUEST_COUNT.labels(
                endpoint=endpoint,
                method=request.method,
                status=str(status),
            ).inc()

            API_REQUEST_DURATION.labels(
                endpoint=endpoint,
                method=request.method,
            ).observe(duration)

        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Añade contexto de request al estado de la app
    para que los handlers puedan acceder al container.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Propaga el container al contexto del request
        if hasattr(request.app.state, "container"):
            request.state.container = request.app.state.container
        return await call_next(request)