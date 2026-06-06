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

        Soporta 3 formatos de mensaje:
          1. "book": snapshot completo con bids/asks arrays
          2. "price_change": delta incremental con best_bid/best_ask
             o price_changes array (sin bids/asks completos)
          3. "last_trade_price": último precio de trade (sin order book)

        Formato esperado del mensaje WS de Polymarket:
        {
            "event_type": "book",
            "market": "<condition_id>",
            "asset_id": "<token_id>",
            "bids": [{"price": "0.76", "size": "100"}, ...],
            "asks": [{"price": "0.77", "size": "150"}, ...],
            "timestamp": "1234567890",
            "neg_risk": false
        }
        """
        try:
            event_type = raw.get("event_type", "")

            # Solo procesamos eventos de tipo "book", "price_change" o "last_trade_price"
            if event_type not in ("book", "price_change", "last_trade_price"):
                return None

            bids = raw.get("bids", [])
            asks = raw.get("asks", [])

            # ── price_change deltas: pueden venir sin bids/asks completos ──
            # price_change events carry best_bid/best_ask at the top level
            # and optionally a price_changes array with incremental updates.
            # We use best_bid/best_ask if available, falling back to bids/asks.
            if event_type == "price_change" and (not bids or not asks):
                best_bid_raw = raw.get("best_bid")
                best_ask_raw = raw.get("best_ask")
                if best_bid_raw is not None and best_ask_raw is not None:
                    best_bid = float(best_bid_raw)
                    best_ask = float(best_ask_raw)
                    # Build synthetic single-entry bids/asks for volume computation
                    bids = [{"price": str(best_bid), "size": "0"}]
                    asks = [{"price": str(best_ask), "size": "0"}]
                else:
                    # Try price_changes array: extract best bid/ask from deltas
                    price_changes = raw.get("price_changes", [])
                    if price_changes:
                        buy_prices = [
                            float(pc.get("price", 0))
                            for pc in price_changes
                            if pc.get("side") in ("BUY", None, "")
                        ]
                        sell_prices = [
                            float(pc.get("price", 1))
                            for pc in price_changes
                            if pc.get("side") in ("SELL", None, "")
                        ]
                        if buy_prices and sell_prices:
                            best_bid = max(buy_prices)
                            best_ask = min(sell_prices)
                            if 0 < best_bid and best_ask < 1:
                                bids = [{"price": str(best_bid), "size": "0"}]
                                asks = [{"price": str(best_ask), "size": "0"}]

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
            volume = sum(float(b.get("size", 0)) for b in bids)

            # Timestamp del mensaje o utcnow si no viene
            ts_raw = raw.get("timestamp")
            if ts_raw:
                ts_int = int(ts_raw)
                # Polymarket CLOB /book returns ms timestamps (13 digits).
                # WebSocket may return seconds (10 digits).
                # Normalize: if > 1e12, treat as ms → divide by 1000.
                if ts_int > 1_000_000_000_000:
                    ts_int = ts_int // 1000
                timestamp = datetime.utcfromtimestamp(ts_int)
            else:
                timestamp = datetime.utcnow()

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
    def parse_neg_risk(raw: dict) -> bool:
        """
        Extrae el flag neg_risk del mensaje de order book.
        Si el mercado es neg_risk (mercados con outcomes mutuamente
        excluyentes), las órdenes DEBEN incluir negRisk=True.
        """
        return bool(raw.get("neg_risk", raw.get("negRisk", False)))

    @staticmethod
    def parse_tick_size(raw: dict) -> str | None:
        """
        Extrae el tick_size de un mensaje WS tick_size_change.
        Este valor DEBE usarse para las órdenes subsiguientes.
        Polymarket puede cambiar tick_size dinámicamente (ej: cerca de 0.04 o 0.96).
        """
        ts = raw.get("tick_size", raw.get("tickSize"))
        if ts is not None:
            return str(ts)
        # También puede venir como "new_tick_size" en tick_size_change
        nts = raw.get("new_tick_size", raw.get("newTickSize"))
        if nts is not None:
            return str(nts)
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
        clob_ids_raw = raw.get("clobTokenIds") or "[]"
        outcomes_raw = raw.get("outcomes", "[]")
        prices_raw = raw.get("outcomePrices", "[]")

        # clobTokenIds is a JSON-encoded list of token ID strings
        # (e.g. '["10609...", "83951..."]'), NOT a native list
        try:
            clob_ids = (
                _json.loads(clob_ids_raw)
                if isinstance(clob_ids_raw, str)
                else clob_ids_raw
            )
        except (_json.JSONDecodeError, TypeError):
            clob_ids = []

        try:
            outcomes = (
                _json.loads(outcomes_raw)
                if isinstance(outcomes_raw, str)
                else outcomes_raw
            )
        except (_json.JSONDecodeError, TypeError):
            outcomes = []
        try:
            prices = (
                _json.loads(prices_raw)
                if isinstance(prices_raw, str)
                else prices_raw
            )
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
            "start_date_iso": raw.get(
                "startDateIso",
                raw.get("start_date_iso", raw.get("startDate", "")),
            ),
            "end_date_iso": raw.get(
                "endDateIso",
                raw.get("end_date_iso", raw.get("endDate", "")),
            ),
        }
