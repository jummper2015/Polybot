# src/domain/value_objects/token_utils.py

"""Utilidad compartida para normalizar token IDs de Polymarket.

El campo yes_token_id puede venir en varios formatos debido a bugs históricos
(trailing commas que convertían strings en tuplas) y diferencias de serialización:

  - str:       "0x1234..."  (formato correcto actual)
  - list:      ["0x1234..."] (de tuplas serializadas por orjson)
  - tuple:     ("0x1234...",) (del bug de trailing comma en dataclass)
  - str JSON:  '["0x1234..."]' (posible doble serialización)
  - dict list: [{"token_id": "0x1234..."}] (del adapter parse_rest_market)
"""

import json as _json


def normalize_token_id(raw_value) -> str:
    """
    Normaliza un token ID desde cualquiera de sus formatos posibles
    a una string hexadecimal limpia.

    Devuelve una string vacía si no se puede extraer un token válido.
    """
    if not raw_value:
        return ""

    # ── Caso 1: string simple ────────────────────────────────────────
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        # Podría ser JSON: '["0x..."]' o '"0x..."'
        if stripped.startswith(("[", "{")):
            try:
                parsed = _json.loads(stripped)
                return _extract_from_collection(parsed)
            except (_json.JSONDecodeError, TypeError):
                pass
        # String hexadecimal normal
        return stripped if stripped.startswith("0x") else stripped

    # ── Caso 2: lista o tupla ─────────────────────────────────────────
    if isinstance(raw_value, (list, tuple)):
        return _extract_from_collection(raw_value)

    # ── Caso 3: otro tipo — convertir a string como fallback ──────────
    return str(raw_value)


def _extract_from_collection(collection) -> str:
    """Extrae el primer token ID de una lista/tupla de elementos."""
    if len(collection) == 0:
        return ""

    first = collection[0]

    if isinstance(first, dict):
        # Formato: [{"token_id": "0x...", "outcome": "Yes"}]
        return str(first.get("token_id", ""))

    return str(first) if first else ""
