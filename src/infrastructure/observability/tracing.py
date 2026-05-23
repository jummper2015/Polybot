# src/infrastructure/observability/tracing.py
"""
OpenTelemetry distributed tracing for PolyBot.

Provides:
  - OTLP exporter configuration (gRPC default, HTTP fallback)
  - Global tracer instance for the application
  - Async context manager `traced()` for creating spans with attributes
  - Init/shutdown lifecycle hooks for bootstrap integration

Environment variables:
  OTEL_EXPORTER_OTLP_ENDPOINT  — OTLP collector endpoint (e.g. http://jaeger:4317)
  OTEL_SERVICE_NAME            — Service name (default: polybot)
  OTEL_TRACES_ENABLED          — Set to 'false' to disable tracing entirely
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, cast

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

logger = structlog.get_logger(__name__)

# ── Globales ──────────────────────────────────────────────────────────

TRACER = trace.get_tracer("polybot")
_tracing_initialized: bool = False


# ── Inicialización ────────────────────────────────────────────────────

def init_tracing(
    service_name: str | None = None,
    otlp_endpoint: str | None = None,
) -> bool:
    """
    Inicializa OpenTelemetry con exportación OTLP.

    Si OTEL_TRACES_ENABLED=false o no hay endpoint configurado,
    retorna False (no-op — los spans igual funcionan pero no se exportan).

    Returns:
        True si el tracing se inicializó correctamente, False si está deshabilitado.
    """
    global _tracing_initialized

    # ── Verificación de habilitación ────────────────────────────────
    enabled = os.environ.get("OTEL_TRACES_ENABLED", "true").lower()
    if enabled in ("false", "0", "no", "off"):
        logger.info("tracing_disabled_by_config")
        return False

    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.info("tracing_disabled_no_endpoint")
        return False

    name = cast(str, service_name or os.environ.get("OTEL_SERVICE_NAME", "polybot"))
    deploy_env = cast(str, os.environ.get("DEPLOY_ENV", "production"))

    # ── Resource ─────────────────────────────────────────────────────
    resource = Resource(attributes={
        "service.name": name,
        "service.version": "1.0.0",
        "deployment.environment": deploy_env,
    })

    # ── Provider + Exporter ──────────────────────────────────────────
    provider = TracerProvider(
        resource=resource,
        sampler=ALWAYS_ON,
    )

    # Elige gRPC o HTTP según el endpoint
    use_http = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower() == "http/protobuf"

    from opentelemetry.sdk.trace.export import SpanExporter
    exporter: SpanExporter

    if use_http:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HTTPExporter,
        )
        exporter = HTTPExporter(
            endpoint=f"{endpoint.rstrip('/')}/v1/traces",
        )
    else:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GRPCExporter,
        )
        exporter = GRPCExporter(
            endpoint=endpoint,
            insecure=os.environ.get("OTEL_INSECURE", "true").lower() != "false",
        )

    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            max_export_batch_size=512,
            schedule_delay_millis=5000,
        )
    )

    trace.set_tracer_provider(provider)
    _tracing_initialized = True

    logger.info(
        "tracing_initialized",
        service_name=name,
        endpoint=endpoint,
        protocol="http" if use_http else "grpc",
    )

    return True


def shutdown_tracing() -> None:
    """
    Flush y apagado limpio del tracer provider.
    Debe llamarse durante el shutdown del container.
    """
    global _tracing_initialized
    if not _tracing_initialized:
        return

    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        try:
            provider.shutdown()
            logger.info("tracing_shutdown_complete")
        except Exception as e:
            logger.error("tracing_shutdown_error", error=str(e))

    _tracing_initialized = False


def is_initialized() -> bool:
    """Verifica si el tracing está activo."""
    return _tracing_initialized


# ── Helpers para spans ────────────────────────────────────────────────

@asynccontextmanager
async def traced(
    name: str,
    kind: SpanKind = SpanKind.INTERNAL,
    **attributes: str,
) -> AsyncIterator[Span]:
    """
    Async context manager para crear un span con atributos.

    Uso:
        async with traced("market_cycle", market_id=m.id, asset="BTC") as span:
            span.set_status(Status(StatusCode.OK))
            # ... trabajo ...

    Si el tracing no está inicializado, el span sigue funcionando
    pero no se exporta (no-op span).
    """
    span = TRACER.start_span(name, kind=kind)
    for k, v in attributes.items():
        if v:  # No setear atributos vacíos
            span.set_attribute(k, str(v)[:256])

    try:
        with trace.use_span(span, end_on_exit=True) as active_span:
            yield active_span
    except Exception as exc:
        span.set_status(Status(StatusCode.ERROR))
        span.record_exception(exception=exc)
        raise


def traced_sync(name: str, **attributes: str):
    """
    Decorador síncrono simple para funciones ligeras.
    Preferir `traced()` async context manager en código asíncrono.

    Uso:
        @traced_sync("my_operation")
        def do_something():
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            with TRACER.start_as_current_span(name) as span:
                for k, v in attributes.items():
                    if v:
                        span.set_attribute(k, str(v)[:256])
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR))
                    span.record_exception(exception=exc)
                    raise
        return wrapper
    return decorator


def get_tracer() -> trace.Tracer:
    """Devuelve el tracer global para uso directo."""
    return TRACER
