# src/infrastructure/cache/redis_client.py

import json
import structlog
from datetime import datetime
from redis.asyncio import Redis

from src.domain.entities.market import Market, Asset, Window, MarketStatus

logger = structlog.get_logger(__name__)

# Prefijos de keys en Redis
KEY_MARKET       = "market:{market_id}"
KEY_ACTIVE_LIST  = "markets:active:{asset}:{window}"


class RedisClient:
    """
    Maneja el estado runtime en Redis.
    Markets activos, locks de ciclo, estado de estrategias.
    """

    def __init__(self, redis: Redis):
        self._redis = redis

    async def set_market(self, market: Market, ttl_seconds: int = 3900) -> None:
        """
        Serializa un Market a JSON y lo guarda con TTL.
        TTL por defecto: 65 minutos (un poco más que el intervalo de re-discovery).
        """
        key  = KEY_MARKET.format(market_id=market.id)
        data = {
            "id":           market.id,
            "asset":        market.asset.value,
            "window":       market.window.value,
            "question":     market.question,
            "status":       market.status.value,
            "yes_token_id": market.yes_token_id,
            "no_token_id":  market.no_token_id,
            "yes_price":    market.yes_price,
            "no_price":     market.no_price,
            "volume_24h":   market.volume_24h,
            "expiry":       market.expiry.isoformat(),
            "discovered_at": market.discovered_at.isoformat(),
        }
        await self._redis.setex(key, ttl_seconds, json.dumps(data))

        # Añade a la lista de activos para búsqueda rápida
        list_key = KEY_ACTIVE_LIST.format(
            asset=market.asset.value,
            window=market.window.value,
        )
        await self._redis.sadd(list_key, market.id)
        await self._redis.expire(list_key, ttl_seconds)

    async def get_market(self, market_id: str) -> Market | None:
        """Recupera un market desde Redis. Devuelve None si no existe o expiró."""
        key  = KEY_MARKET.format(market_id=market_id)
        data = await self._redis.get(key)
        if not data:
            return None
        return self._deserialize(json.loads(data))

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
                market_ids = await self._redis.smembers(list_key)

                for mid in market_ids:
                    market = await self.get_market(mid.decode())
                    if market:
                        markets.append(market)

        return markets

    def _deserialize(self, data: dict) -> Market:
        """Reconstruye una entidad Market desde un dict JSON."""
        return Market(
            id           = data["id"],
            asset        = Asset(data["asset"]),
            window       = Window(data["window"]),
            question     = data["question"],
            status       = MarketStatus(data["status"]),
            yes_token_id = data["yes_token_id"],
            no_token_id  = data["no_token_id"],
            yes_price    = data["yes_price"],
            no_price     = data["no_price"],
            volume_24h   = data["volume_24h"],
            expiry       = datetime.fromisoformat(data["expiry"]),
            discovered_at= datetime.fromisoformat(data["discovered_at"]),
        )
        # Añadir a src/infrastructure/cache/redis_client.py

import dataclasses

KEY_WS_STATE = "ws:state:{market_id}"


class RedisClient:
    # ... (métodos anteriores de B6) ...

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
        await self._redis.setex(key, ttl_seconds, json.dumps(data))

    async def get_ws_state(self, market_id: str) -> dict | None:
        """Recupera el estado WS de un mercado. Dict simple, no la entidad."""
        key  = KEY_WS_STATE.format(market_id=market_id)
        data = await self._redis.get(key)
        return json.loads(data) if data else None

    async def delete_ws_state(self, market_id: str) -> None:
        """Elimina el estado WS al desuscribirse."""
        key = KEY_WS_STATE.format(market_id=market_id)
        await self._redis.delete(key)

        # Añadir a src/infrastructure/cache/redis_client.py

KEY_PAPER_BALANCE  = "paper:balance"
KEY_WS_LAST_PRICE  = "ws:price:{market_id}"

class RedisClient:
    # ... (métodos anteriores) ...

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
        market_id:  str,
        yes_price:  float,
        spread:     float,
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
        await self._redis.hset(key, mapping=data)
        await self._redis.expire(key, 300)  # 5 minutos