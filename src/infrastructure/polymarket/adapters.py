# src/infrastructure/polymarket/adapters.py

import json as _json
from datetime import datetime

import structlog

from src.domain.value_objects.market_tick import MarketTick

logger = structlog.get_logger(__name__)


class PolymarketAdapter:
    """
    Convierte mensajes raw de la API/WS de Polymarket
    en objetos de dominio (MarketTick).
    Centraliza TODO el parsing — si la API cambia, solo se toca aquí.
    """

    @staticmethod
    def parse_orderbook_message(market_id: str, raw: dict) -> MarketTick | None:
        """
        Parsea un mensaje de order book del WebSocket.
        Devuelve None si el mensaje no tiene datos de precio válidos.

        Formato esperado del mensaje WS de Polymarket:
        {
            "event_type": "book",
            "market": "<condition_id>",
            "asset_id": "<token_id>",
            "bids": [{"price": "0.76", "size": "100"}, ...],
            "asks": [{"price": "0.77", "size": "150"}, ...],
            "timestamp": "1234567890"
        }
        """
        try:
            event_type = raw.get("event_type", "")

            # Solo procesamos eventos de tipo "book" o "price_change"
            if event_type not in ("book", "price_change", "last_trade_price"):
                return None

            bids = raw.get("bids", [])
            asks = raw.get("asks", [])

            # Necesitamos al menos un bid y un ask para calcular spread
            if not bids or not asks:
                return None

            # Mejor bid (mayor precio de compra) y mejor ask (menor precio de venta)
            best_bid = max(float(b["price"]) for b in bids)
            best_ask = min(float(a["price"]) for a in asks)

            # Precio YES = mid price del order book
            yes_price = (best_bid + best_ask) / 2
            no_price  = 1.0 - yes_price          # En mercados binarios: YES + NO = 1
            spread    = best_ask - best_bid

            # Volumen total de bids como proxy de liquidez
            volume = sum(float(b["size"]) for b in bids)

            # Timestamp del mensaje o utcnow si no viene
            ts_raw = raw.get("timestamp")
            timestamp = (
                datetime.utcfromtimestamp(int(ts_raw))
                if ts_raw
                else datetime.utcnow()
            )

            return MarketTick(
                market_id  = market_id,
                yes_price  = round(yes_price, 4),
                no_price   = round(no_price,  4),
                best_bid   = round(best_bid,  4),
                best_ask   = round(best_ask,  4),
                spread     = round(spread,    4),
                volume_24h = round(volume,    2),
                timestamp  = timestamp,
            )

        except (KeyError, ValueError, TypeError) as e:
            logger.warning(
                "orderbook_parse_failed",
                market_id=market_id,
                error=str(e),
                raw_keys=list(raw.keys()),
            )
            return None

    @staticmethod
    def parse_rest_market(raw: dict) -> dict:
        """
        Normaliza la respuesta REST de /events (markets anidados) a un formato
        consistente para MarketService._parse_market().

        El endpoint /events devuelve markets con:
          - conditionId (camelCase, no condition_id)
          - clobTokenIds (array de strings, no objetos token)
          - outcomes / outcomePrices (JSON strings de arrays)
          - startDateIso / endDateIso
          - slug (contiene el timeframe: "5m", "15m")
        """
        # --- Construir lista de tokens a partir de clobTokenIds + outcomes + outcomePrices ---
        tokens: list[dict] = []
        clob_ids = raw.get("clobTokenIds") or []
        outcomes_raw = raw.get("outcomes", "[]")
        prices_raw = raw.get("outcomePrices", "[]")

        try:
            outcomes = _json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        except (_json.JSONDecodeError, TypeError):
            outcomes = []
        try:
            prices = _json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        except (_json.JSONDecodeError, TypeError):
            prices = []

        for i, token_id in enumerate(clob_ids):
            token_entry: dict = {"token_id": str(token_id)}
            if i < len(outcomes):
                token_entry["outcome"] = outcomes[i]
            else:
                # Fallback: sin outcomes, asumir "Yes"/"No" por posición
                token_entry["outcome"] = "Yes" if i == 0 else "No"
            if i < len(prices):
                token_entry["price"] = prices[i]
            tokens.append(token_entry)

        # --- Volumen: probar volume24hr primero, luego liquidity como proxy ---
        try:
            volume = float(raw.get("volume24hr", 0) or 0)
        except (ValueError, TypeError):
            volume = 0.0
        if volume == 0.0:
            try:
                volume = float(raw.get("liquidity", 0) or 0)
            except (ValueError, TypeError):
                volume = 0.0

        return {
            "condition_id":   raw.get("conditionId", raw.get("condition_id", raw.get("id", ""))),
            "question":       raw.get("question", ""),
            "slug":           raw.get("slug", ""),
            "active":         raw.get("active", False),
            "tokens":         tokens,
            "volume24hr":     volume,
            "start_date_iso": raw.get("startDateIso", raw.get("start_date_iso", raw.get("startDate", ""))),
            "end_date_iso":   raw.get("endDateIso",   raw.get("end_date_iso",   raw.get("endDate",   ""))),
        }
