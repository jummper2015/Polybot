# src/infrastructure/security/rate_limiter.py

import time

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

# Ventana de tiempo para el rate limit (segundos)
RATE_LIMIT_WINDOW_SECONDS = 3600   # 1 hora
# Máximo de órdenes reales en esa ventana
RATE_LIMIT_MAX_ORDERS     = 10

# Keys de Redis para el rate limiter
KEY_RATE_LIMIT = "security:rate_limit:real_orders"
KEY_RATE_LIMIT_DAILY = "security:rate_limit:daily_orders:{date}"


class RateLimiter:
    """
    Sliding window rate limiter para órdenes reales.
    Usa Redis sorted sets para implementar ventana deslizante.
    Cada entry es: score=timestamp, member=uuid_de_orden.

    Principio:
    - Añade cada orden como entry con timestamp como score
    - Elimina entries más viejos que la ventana
    - Cuenta los restantes — si >= max, rechaza
    """

    def __init__(self, redis: Redis):
        self._redis = redis

    async def check_and_record(
        self,
        order_id:   str,
        market_id:  str,
    ) -> tuple[bool, str]:
        """
        Verifica si se puede ejecutar una orden real y la registra.
        Devuelve (allowed: bool, reason: str).
        Operación atómica via pipeline de Redis.
        """
        now        = time.time()
        window_start = now - RATE_LIMIT_WINDOW_SECONDS

        # Pipeline: todas las operaciones en una sola roundtrip
        async with self._redis.pipeline(transaction=True) as pipe:
            # 1. Elimina entries fuera de la ventana (más viejos que 1h)
            await pipe.zremrangebyscore(KEY_RATE_LIMIT, "-inf", window_start)
            # 2. Cuenta entries en la ventana actual
            await pipe.zcard(KEY_RATE_LIMIT)
            results = await pipe.execute()

        current_count = results[1]

        if current_count >= RATE_LIMIT_MAX_ORDERS:
            reason = (
                f"rate_limit_exceeded: {current_count}/{RATE_LIMIT_MAX_ORDERS} "
                f"órdenes reales en la última hora. "
                f"Espera antes de la próxima operación."
            )
            logger.warning(
                "rate_limit_blocked",
                count=current_count,
                max=RATE_LIMIT_MAX_ORDERS,
                market_id=market_id,
            )
            return False, reason

        # Registra la nueva orden en el sorted set
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.zadd(KEY_RATE_LIMIT, {order_id: now})
            # TTL: expira automáticamente después de la ventana
            await pipe.expire(KEY_RATE_LIMIT, RATE_LIMIT_WINDOW_SECONDS + 60)
            await pipe.execute()

        logger.info(
            "rate_limit_ok",
            count=current_count + 1,
            max=RATE_LIMIT_MAX_ORDERS,
            remaining=RATE_LIMIT_MAX_ORDERS - current_count - 1,
        )
        return True, f"rate_limit_ok: {current_count + 1}/{RATE_LIMIT_MAX_ORDERS}"

    async def get_current_count(self) -> int:
        """
        Devuelve el número de órdenes reales en la última hora.
        Usado por el health check y el status de Telegram.
        """
        now          = time.time()
        window_start = now - RATE_LIMIT_WINDOW_SECONDS

        await self._redis.zremrangebyscore(
            KEY_RATE_LIMIT, "-inf", window_start
        )
        return await self._redis.zcard(KEY_RATE_LIMIT)

    async def get_remaining(self) -> int:
        """Órdenes reales restantes en la ventana actual."""
        count = await self.get_current_count()
        return max(0, RATE_LIMIT_MAX_ORDERS - count)

    async def reset(self) -> None:
        """
        Resetea el rate limiter.
        Solo para uso en tests y en situaciones de emergencia.
        Genera audit log cuando se usa.
        """
        await self._redis.delete(KEY_RATE_LIMIT)
        logger.warning("rate_limiter_reset_manually")
