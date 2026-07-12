# src/infrastructure/cache/redis_client.py

from datetime import datetime
from typing import TYPE_CHECKING

import orjson
import structlog
from redis.asyncio import Redis

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window
from src.domain.value_objects.token_utils import normalize_token_id

if TYPE_CHECKING:
    from src.domain.value_objects.ws_state import WSMarketState

logger = structlog.get_logger(__name__)

# ── Prefijos de keys en Redis ─────────────────────────────────────────
KEY_MARKET       = "market:{market_id}"
KEY_ACTIVE_LIST  = "markets:active:{asset}:{window}"
KEY_WS_STATE     = "ws:state:{market_id}"
KEY_PAPER_BALANCE = "paper:balance"
KEY_WS_LAST_PRICE = "ws:price:{market_id}"
KEY_ORDERBOOK     = "orderbook:{market_id}"
KEY_MARKET_META   = "market:meta:{market_id}"
KEY_CLOB_MARKET_INFO = "clob:market_info:{condition_id}"
# Ola 2.5: rolling buffer de precios recientes por mercado (últimos N ticks).
# Se usa desde paper_handler para computar realized_volatility y regime.
KEY_RECENT_TICKS  = "ws:recent_ticks:{market_id}"
RECENT_TICKS_MAX  = 20     # Buffer size (mismo que StrategyState.tick_buffer)
RECENT_TICKS_TTL  = 300    # 5 min — si no hay ticks, el buffer expira solo


class RedisClient:
    """
    Maneja el estado runtime en Redis.
    Markets activos, estado WebSocket, balance paper, locks de ciclo.
    """

    def __init__(self, redis: Redis):
        self._redis = redis

    # ──────────────────────────────────────────────────────────────────
    # Market Discovery (B6)
    # ──────────────────────────────────────────────────────────────────

    async def set_market(self, market: Market, ttl_seconds: int = 3900) -> None:
        """
        Serializa un Market a JSON y lo guarda con TTL.
        TTL por defecto: 65 minutos (un poco más que el intervalo de re-discovery).
        """
        key  = KEY_MARKET.format(market_id=market.id)
        data = {
            "id":            market.id,
            "asset":         market.asset.value,
            "window":        market.window.value,
            "question":      market.question,
            "status":        market.status.value,
            "yes_token_id":  market.yes_token_id,
            "no_token_id":   market.no_token_id,
            "yes_price":     market.yes_price,
            "no_price":      market.no_price,
            "volume_24h":    market.volume_24h,
            "expiry":        market.expiry.isoformat(),
            "discovered_at": market.discovered_at.isoformat(),
            "neg_risk":      market.neg_risk,
            "tick_size":     market.tick_size,
            "min_order_size": market.min_order_size,
        }
        await self._redis.setex(key, ttl_seconds, orjson.dumps(data).decode())

        # Añade a la lista de activos para búsqueda rápida
        list_key = KEY_ACTIVE_LIST.format(
            asset=market.asset.value,
            window=market.window.value,
        )
        await self._redis.sadd(list_key, market.id)  # type: ignore[misc]
        await self._redis.expire(list_key, ttl_seconds)  # type: ignore[misc]  # noqa: E501

    async def get_market(self, market_id: str) -> Market | None:
        """Recupera un market desde Redis. Devuelve None si no existe o expiró."""
        key  = KEY_MARKET.format(market_id=market_id)
        data = await self._redis.get(key)
        if not data:
            return None
        return self._deserialize(orjson.loads(data))

    async def clear_active_markets_lists(self) -> None:
        """
        Borra todos los sets `markets:active:{asset}:{window}`.

        Llamado al inicio de cada `discover_markets` para evitar que ids
        de markets ya rotados (los crypto M5/M15 viven 5-15 min) sobrevivan
        en el set y aparezcan en el dashboard sin sus blobs reales.
        """
        for a in Asset:
            for w in Window:
                list_key = KEY_ACTIVE_LIST.format(asset=a.value, window=w.value)
                await self._redis.delete(list_key)

    async def get_active_markets(
        self,
        asset:  str | None = None,
        window: str | None = None,
    ) -> list[Market]:
        """
        Recupera lista de markets activos desde Redis.
        Filtra por asset y/o window si se especifican.
        """
        markets = []
        assets  = [asset]  if asset  else [a.value for a in Asset]
        windows = [window] if window else [w.value for w in Window]

        for a in assets:
            for w in windows:
                list_key = KEY_ACTIVE_LIST.format(asset=a, window=w)
                market_ids = await self._redis.smembers(list_key)  # type: ignore[misc]  # noqa: E501

                for mid in market_ids:
                    # redis-py >= 4.0 devuelve str; versiones antiguas devuelven bytes
                    mid_str = mid.decode() if isinstance(mid, bytes) else mid
                    market = await self.get_market(mid_str)
                    if market:
                        markets.append(market)

        return markets

    def _deserialize(self, data: dict) -> Market:
        """Reconstruye una entidad Market desde un dict JSON."""
        return Market(
            id            = data["id"],
            asset         = Asset(data["asset"]),
            window        = Window(data["window"]),
            question      = data["question"],
            status        = MarketStatus(data["status"]),
            yes_token_id  = normalize_token_id(data["yes_token_id"]),
            no_token_id   = normalize_token_id(data["no_token_id"]),
            yes_price     = data["yes_price"],
            no_price      = data["no_price"],
            volume_24h    = data["volume_24h"],
            expiry        = datetime.fromisoformat(data["expiry"]),
            discovered_at = datetime.fromisoformat(data["discovered_at"]),
            neg_risk      = data.get("neg_risk", False),
            tick_size     = data.get("tick_size", "0.01"),
            min_order_size = data.get("min_order_size", 1.0),
        )

    # ──────────────────────────────────────────────────────────────────
    # Market Metadata (tick_size, neg_risk, min_order_size)
    # ──────────────────────────────────────────────────────────────────

    async def set_market_metadata(
        self,
        market_id: str,
        updates:   dict,
        ttl_seconds: int = 3900,
    ) -> None:
        """
        Guarda metadatos adicionales del mercado (tick_size, neg_risk,
        min_order_size) en Redis. Hace merge parcial con los datos
        existentes — no sobrescribe campos que no se incluyen en updates.
        """
        key = KEY_MARKET_META.format(market_id=market_id)
        existing = await self._redis.get(key)  # type: ignore[misc]  # noqa: E501
        data = orjson.loads(existing) if existing else {}
        data.update(updates)
        await self._redis.setex(key, ttl_seconds, orjson.dumps(data).decode())

    async def get_market_metadata(self, market_id: str) -> dict:
        """
        Recupera metadatos del mercado desde Redis.
        Devuelve dict con tick_size, neg_risk, min_order_size (o vacío).
        """
        key = KEY_MARKET_META.format(market_id=market_id)
        data = await self._redis.get(key)
        return orjson.loads(data) if data else {}

    # ──────────────────────────────────────────────────────────────────
    # CLOB V2 Market Info (fees dinámicos por mercado)
    # ──────────────────────────────────────────────────────────────────

    async def set_clob_market_info(
        self,
        condition_id: str,
        info: dict,
        ttl_seconds: int = 300,
    ) -> None:
        """
        Cachea la respuesta de ClobClient.get_clob_market_info para un mercado.
        TTL 5 min por defecto: fees dinámicos pueden cambiar, pero recalcularlos
        en cada estimación es costoso.
        """
        key = KEY_CLOB_MARKET_INFO.format(condition_id=condition_id)
        await self._redis.setex(key, ttl_seconds, orjson.dumps(info).decode())

    async def get_clob_market_info(self, condition_id: str) -> dict | None:
        """
        Recupera la info de mercado cacheada. None si no existe o expiró.
        """
        key = KEY_CLOB_MARKET_INFO.format(condition_id=condition_id)
        data = await self._redis.get(key)
        return orjson.loads(data) if data else None

    # ──────────────────────────────────────────────────────────────────
    # WebSocket State
    # ──────────────────────────────────────────────────────────────────

    async def set_ws_state(
        self,
        market_id: str,
        state: "WSMarketState",
        ttl_seconds: int = 120,
    ) -> None:
        """
        Guarda el estado WS de un mercado en Redis.
        TTL corto (2 min) — si el bot muere, el estado expira solo.
        """
        key  = KEY_WS_STATE.format(market_id=market_id)

        # Serializa solo los campos primitivos (MarketTick se guarda aparte)
        data = {
            "market_id":          state.market_id,
            "status":             state.status.value,
            "reconnect_attempts": state.reconnect_attempts,
            "last_message_at":    state.last_message_at.isoformat()
                                  if state.last_message_at else None,
            "connected_at":       state.connected_at.isoformat()
                                  if state.connected_at else None,
            "error":              state.error,
        }
        await self._redis.setex(key, ttl_seconds, orjson.dumps(data).decode())

    async def get_ws_state(self, market_id: str) -> dict | None:
        """Recupera el estado WS de un mercado. Dict simple, no la entidad."""
        key  = KEY_WS_STATE.format(market_id=market_id)
        data = await self._redis.get(key)
        return orjson.loads(data) if data else None

    async def delete_ws_state(self, market_id: str) -> None:
        """Elimina el estado WS al desuscribirse."""
        key = KEY_WS_STATE.format(market_id=market_id)
        await self._redis.delete(key)  # type: ignore[misc]  # noqa: E501

    # ──────────────────────────────────────────────────────────────────
    # Ola 2.5: Rolling tick buffer (para volatility / regime dinámicos)
    # ──────────────────────────────────────────────────────────────────

    async def push_recent_tick(
        self,
        market_id: str,
        price:     float,
        best_bid:  float | None = None,
        best_ask:  float | None = None,
    ) -> None:
        """
        Empuja un tick al buffer circular de precios recientes para `market_id`.

        Implementación: LPUSH + LTRIM 0..N-1 + EXPIRE. El buffer más reciente
        queda en el índice 0. Fuente única de tick history usada por
        paper_handler para computar realized_volatility en tiempo real.

        Args:
            price: yes_price del tick
            best_bid, best_ask: opcionales; si están se usa el mid; si no,
                                usamos `price` directamente
        """
        key = KEY_RECENT_TICKS.format(market_id=market_id)

        # Preferir mid = (bid+ask)/2 cuando esté disponible (más estable
        # que yes_price para computar volatility, alineado con features.py
        # compute_realized_volatility).
        if best_bid is not None and best_ask is not None and best_bid > 0 and best_ask > 0:
            mid = (best_bid + best_ask) / 2
        else:
            mid = price

        entry = orjson.dumps({
            "mid":   round(mid, 6),
            "price": round(price, 6),
            "ts_ms": int(datetime.now().timestamp() * 1000),
        }).decode()

        # Pipeline atómico: LPUSH + LTRIM + EXPIRE en un solo round-trip
        pipe = self._redis.pipeline()
        pipe.lpush(key, entry)
        pipe.ltrim(key, 0, RECENT_TICKS_MAX - 1)
        pipe.expire(key, RECENT_TICKS_TTL)
        await pipe.execute()

    async def get_recent_ticks(
        self,
        market_id: str,
        limit:     int = RECENT_TICKS_MAX,
    ) -> list[dict]:
        """
        Retorna los N ticks más recientes del buffer, en orden
        cronológico ascendente (más antiguo primero — orden natural
        para computar returns).

        Returns:
            Lista de dicts `{mid, price, ts_ms}`. Lista vacía si no hay
            buffer (mercado nuevo o TTL expirado).
        """
        key  = KEY_RECENT_TICKS.format(market_id=market_id)
        raw  = await self._redis.lrange(key, 0, limit - 1)  # type: ignore[misc]
        # LRANGE devuelve el más reciente primero (LPUSH en cabeza) →
        # invertimos para orden cronológico ascendente.
        return [orjson.loads(r) for r in reversed(raw)] if raw else []

    async def clear_recent_ticks(self, market_id: str) -> None:
        """Limpia el buffer de ticks recientes (usado en tests / recovery)."""
        key = KEY_RECENT_TICKS.format(market_id=market_id)
        await self._redis.delete(key)  # type: ignore[misc]

    # ──────────────────────────────────────────────────────────────────
    # Paper Trading Balance & Prices
    # ──────────────────────────────────────────────────────────────────

    async def set_paper_balance(self, balance: float) -> None:
        """
        Persiste el balance virtual en Redis.
        Sin TTL — el balance es permanente hasta que se resetee.
        """
        await self._redis.set(KEY_PAPER_BALANCE, str(round(balance, 4)))

    async def get_paper_balance(self) -> float | None:
        """Recupera el balance virtual. None si no existe."""
        value = await self._redis.get(KEY_PAPER_BALANCE)
        return float(value) if value else None

    async def set_last_tick_price(
        self,
        market_id: str,
        yes_price: float,
        spread:    float,
        best_bid:  float = 0.0,
        best_ask:  float = 0.0,
        volume_24h: float = 0.0,
    ) -> None:
        """
        Guarda el último precio conocido de un mercado.
        Usado por PaperTradingHandler para calcular slippage.
        TTL de 5 minutos — si no hay tick nuevo, el precio está stale.
        """
        key  = KEY_WS_LAST_PRICE.format(market_id=market_id)
        data = {
            "last_yes_price": str(yes_price),
            "last_spread":    str(spread),
        }
        if best_bid > 0:
            data["best_bid"] = str(best_bid)
        if best_ask > 0:
            data["best_ask"] = str(best_ask)
        if volume_24h > 0:
            data["volume_24h"] = str(volume_24h)
        await self._redis.hset(key, mapping=data)  # type: ignore[misc]
        await self._redis.expire(key, 300)  # 5 minutos  # type: ignore[misc]  # noqa: E501

    async def get_last_tick_price(self, market_id: str) -> dict | None:
        """
        Recupera el último precio/spread de un mercado desde Redis.

        Returns dict with last_yes_price, last_spread, and optionally
        best_bid, best_ask, volume_24h. None if no data or expired.
        """
        key  = KEY_WS_LAST_PRICE.format(market_id=market_id)
        data = await self._redis.hgetall(key)  # type: ignore[misc]  # noqa: E501
        return {
            k.decode() if isinstance(k, bytes) else k:
            float(v.decode() if isinstance(v, bytes) else v)
            for k, v in data.items()
        } if data else None

    # ──────────────────────────────────────────────────────────────────
    # Order Book (para dashboard en tiempo real)
    # ──────────────────────────────────────────────────────────────────

    async def set_orderbook(
        self,
        market_id: str,
        bids:      list[dict],
        asks:      list[dict],
        ttl_seconds: int = 120,
    ) -> None:
        """
        Guarda el order book completo (bids + asks) en Redis.
        Cada entry es {"price": str, "size": str} tal como viene del WS.
        TTL corto (2 min) — el dashboard lo refresca cada 10s.
        """
        key  = KEY_ORDERBOOK.format(market_id=market_id)
        data = {
            "bids": bids,
            "asks": asks,
            "updated_at": datetime.utcnow().isoformat(),
        }
        await self._redis.setex(key, ttl_seconds, orjson.dumps(data).decode())

    async def get_orderbook(self, market_id: str) -> dict | None:
        """
        Recupera el order book de un mercado desde Redis.
        Devuelve dict con bids, asks, updated_at o None si no hay datos.
        """
        key  = KEY_ORDERBOOK.format(market_id=market_id)
        data = await self._redis.get(key)
        return orjson.loads(data) if data else None
