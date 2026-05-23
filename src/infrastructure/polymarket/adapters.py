# src/infrastructure/polymarket/adapters.py

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
        Normaliza la respuesta REST de /markets a un formato
        consistente para MarketService._parse_market().
        Convierte campos de la API al formato esperado por el dominio.
        """
        return {
            "condition_id":  raw.get("condition_id", raw.get("id", "")),
            "question":      raw.get("question", ""),
            "active":        raw.get("active", False),
            "tokens":        raw.get("tokens", []),
            "volume24hr":    float(raw.get("volume24hr", raw.get("volume", 0))),
            "start_date_iso": raw.get("startDateIso", raw.get("start_date_iso", "")),
            "end_date_iso":   raw.get("endDateIso",   raw.get("end_date_iso",   "")),
        }
