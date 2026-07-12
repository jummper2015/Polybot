# src/infrastructure/observability/logging.py

import logging
import sys

import structlog

from src.infrastructure.security.log_sanitizer import LogSanitizer


def _safe_add_logger_name(logger, method_name, event_dict):
    """
    Safe logger name processor compatible with PrintLoggerFactory.
    structlog.stdlib.add_logger_name requires a stdlib Logger with .name,
    but PrintLogger (used in dev) doesn't have it.
    """
    event_dict["logger"] = getattr(logger, "name", "polybot")
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    """
    Configura structlog con:
    - Sanitización automática de datos sensibles
    - Timestamps ISO 8601
    - Formato JSON en producción, pretty en desarrollo
    - Level configurable desde env
    """

    # Processors en orden (cada uno transforma el event_dict)
    processors = [
        # 1. Añade timestamp ISO 8601
        structlog.processors.TimeStamper(fmt="iso", utc=True),

        # 2. Añade nombre del logger y nivel
        structlog.stdlib.add_log_level,
        _safe_add_logger_name,

        # 3. Sanitiza datos sensibles (ANTES de serializar)
        LogSanitizer.structlog_processor,

        # 4. Formatea excepciones
        structlog.processors.format_exc_info,

        # 5. Serializa a JSON (producción) o pretty (desarrollo)
        structlog.processors.JSONRenderer()
        if _is_production()
        else structlog.dev.ConsoleRenderer(colors=True),
    ]

    structlog.configure(
        processors=processors,  # type: ignore[arg-type]  # structlog typing mismatch with mixed processor types
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # También configura el logging estándar para librerías (sqlalchemy, httpx, etc.)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    # Silencia loggers muy verbosos
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    structlog.get_logger(__name__).info(
        "logging_configured",
        log_level=log_level,
        format="json" if _is_production() else "pretty",
    )


def _is_production() -> bool:
    """
    Detecta si estamos en producción.
    En producción: JSON puro (fácil de parsear por ELK, Datadog, etc.)
    En desarrollo: pretty print con colores.
    """
    import os
    return os.environ.get("ENV", "development").lower() == "production"
