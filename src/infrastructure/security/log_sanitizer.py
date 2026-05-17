# src/infrastructure/security/log_sanitizer.py

import re
from src.infrastructure.security.secure_config import SENSITIVE_KEYS

# Patrones que identifican valores sensibles aunque no vengan de env vars
SENSITIVE_PATTERNS = [
    re.compile(r"0x[a-fA-F0-9]{64}"),          # Private keys (hex 64 chars)
    re.compile(r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-"
               r"[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-"
               r"[a-fA-F0-9]{12}"),             # UUIDs (orden IDs — OK mostrar)
]

REDACTED = "[REDACTED]"


class LogSanitizer:
    """
    Sanitiza dicts de log antes de que lleguen a structlog.
    Elimina o enmascara cualquier valor potencialmente sensible.
    Se integra como processor en la pipeline de structlog.
    """

    @staticmethod
    def sanitize_dict(data: dict) -> dict:
        """
        Recursivamente sanitiza un dict eliminando claves sensibles.
        Seguro para usar en cualquier nivel de anidamiento.
        """
        result = {}
        for key, value in data.items():
            # Si la clave es sensible → redactar el valor
            if key.upper() in SENSITIVE_KEYS:
                result[key] = REDACTED
                continue

            # Si el valor es un dict → recursivo
            if isinstance(value, dict):
                result[key] = LogSanitizer.sanitize_dict(value)
                continue

            # Si el valor es string → verificar patrones sensibles
            if isinstance(value, str):
                result[key] = LogSanitizer.sanitize_string(value)
                continue

            result[key] = value

        return result

    @staticmethod
    def sanitize_string(value: str) -> str:
        """
        Enmascara private keys en strings.
        Permite UUIDs (order_ids) y otros hex normales.
        """
        # Private key hex de 64 chars → redactar
        if re.search(r"0x[a-fA-F0-9]{64}", value):
            return re.sub(r"0x[a-fA-F0-9]{64}", REDACTED, value)
        return value

    @staticmethod
    def structlog_processor(
        logger, method: str, event_dict: dict
    ) -> dict:
        """
        Processor de structlog que sanitiza automáticamente cada log entry.
        Se registra en la pipeline de structlog en bootstrap.
        """
        return LogSanitizer.sanitize_dict(event_dict)