# src/infrastructure/polymarket/ws_client.py

import asyncio
import json
from typing import Awaitable, Callable

import structlog
import websockets

from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.ws_state import WSConnectionStatus, WSMarketState
from src.infrastructure.cache.redis_client import RedisClient
from src.infrastructure.observability.metrics import (
    WS_CONNECTED,
    WS_ERRORS,
    WS_RECONNECTS,
    WS_TICKS_RECEIVED,
)
from src.infrastructure.polymarket.adapters import PolymarketAdapter

logger = structlog.get_logger(__name__)

# URL base del WebSocket de Polymarket
WS_BASE_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Configuración de reconexión
MAX_RECONNECT_ATTEMPTS = 10
BACKOFF_BASE_SECONDS   = 1.0
BACKOFF_MAX_SECONDS    = 60.0
PING_TIMEOUT_SECONDS   = 30

# Mínimo cambio de precio para generar un nuevo tick (evita ruido)
MIN_PRICE_CHANGE = 0.001

# Tipo del callback que recibe ticks
TickCallback = Callable[[MarketTick], Awaitable[None]]


class PolymarketWSClient:
    """
    Cliente WebSocket para recibir order book de Polymarket en tiempo real.
    Gestiona reconexión automática, filtrado de ticks y estado por mercado.
    """

    def __init__(self, redis: RedisClient):
        self._redis      = redis
        self._adapter    = PolymarketAdapter()

        # Registro de subscripciones activas: market_id → Task
        self._subscriptions: dict[str, asyncio.Task] = {}

        # Estado de conexión por mercado
        self._states: dict[str, WSMarketState] = {}

    # ------------------------------------------------------------------
    # API PÚBLICA
    # ------------------------------------------------------------------

    async def subscribe(self, market_id: str, callback: TickCallback) -> None:
        """
        Inicia la subscripción al order book de un mercado.
        Lanza una Task asyncio que vive hasta que se llame unsubscribe().
        """
        if market_id in self._subscriptions:
            logger.warning("already_subscribed", market_id=market_id)
            return

        state = WSMarketState(market_id=market_id)
        self._states[market_id] = state

        # Crea y registra la tarea de escucha
        task = asyncio.create_task(
            self._listen_loop(market_id, callback, state),
            name=f"ws_{market_id}",
        )
        self._subscriptions[market_id] = task

        logger.info("subscribed", market_id=market_id)

    async def unsubscribe(self, market_id: str) -> None:
        """
        Detiene la subscripción de un mercado y limpia su estado.
        """
        task = self._subscriptions.pop(market_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self._states.pop(market_id, None)
        await self._redis.delete_ws_state(market_id)

        logger.info("unsubscribed", market_id=market_id)

    async def get_state(self, market_id: str) -> WSMarketState | None:
        """Devuelve el estado actual de la conexión WS para un mercado."""
        return self._states.get(market_id)

    async def unsubscribe_all(self) -> None:
        """Detiene todas las subscripciones activas (usado en shutdown)."""
        market_ids = list(self._subscriptions.keys())
        for market_id in market_ids:
            await self.unsubscribe(market_id)

    # ------------------------------------------------------------------
    # LOOP DE ESCUCHA CON RECONEXIÓN
    # ------------------------------------------------------------------

    async def _listen_loop(
        self,
        market_id: str,
        callback:  TickCallback,
        state:     WSMarketState,
    ) -> None:
        """
        Loop principal de escucha para un mercado.
        Gestiona reconexión automática con backoff exponencial.
        Se ejecuta hasta que la tarea sea cancelada.
        """
        log = logger.bind(market_id=market_id)

        while True:  # Loop externo: reconexión
            try:
                await self._connect_and_listen(market_id, callback, state, log)

            except asyncio.CancelledError:
                # Cancelación explícita → salimos limpiamente
                log.info("ws_cancelled")
                state.status = WSConnectionStatus.DISCONNECTED
                break

            except Exception as e:
                state.record_reconnecting(str(e))
                WS_ERRORS.labels(market_id=market_id).inc()
                WS_RECONNECTS.labels(market_id=market_id).inc()

                if state.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                    state.status = WSConnectionStatus.FAILED
                    log.error(
                        "ws_max_reconnects_reached",
                        attempts=state.reconnect_attempts,
                        error=str(e),
                    )
                    break

                # Backoff exponencial: 1s, 2s, 4s, 8s... máx 60s
                wait = min(
                    BACKOFF_BASE_SECONDS * (2 ** (state.reconnect_attempts - 1)),
                    BACKOFF_MAX_SECONDS,
                )
                log.warning(
                    "ws_reconnecting",
                    attempt=state.reconnect_attempts,
                    wait_seconds=wait,
                    error=str(e),
                )
                await asyncio.sleep(wait)

    async def _connect_and_listen(
        self,
        market_id: str,
        callback:  TickCallback,
        state:     WSMarketState,
        log,
    ) -> None:
        """
        Establece la conexión WS, se subscribe al mercado
        y procesa mensajes en loop hasta desconexión.
        """
        state.status = WSConnectionStatus.CONNECTING
        url = WS_BASE_URL

        log.info("ws_connecting", url=url)

        async with websockets.connect(
            url,
            ping_interval=20,        # Envía ping cada 20s
            ping_timeout=PING_TIMEOUT_SECONDS,
            close_timeout=10,
        ) as ws:

            # Marca conexión establecida
            state.record_connected()
            WS_CONNECTED.labels(market_id=market_id).set(1)
            log.info("ws_connected")

            # Envía mensaje de subscripción al order book del mercado
            subscribe_msg = {
                "type":    "subscribe",
                "channel": "orderbook",
                "markets": [market_id],
            }
            await ws.send(json.dumps(subscribe_msg))
            log.debug("ws_subscribed", market_id=market_id)

            # Inicia tarea paralela para detectar stale connections
            stale_checker = asyncio.create_task(
                self._stale_checker(market_id, state, ws, log)
            )

            try:
                async for raw_message in ws:
                    await self._process_message(
                        raw_message, market_id, callback, state, log
                    )
            finally:
                stale_checker.cancel()
                WS_CONNECTED.labels(market_id=market_id).set(0)

    # ------------------------------------------------------------------
    # PROCESAMIENTO DE MENSAJES
    # ------------------------------------------------------------------

    async def _process_message(
        self,
        raw_message: str,
        market_id:   str,
        callback:    TickCallback,
        state:       WSMarketState,
        log,
    ) -> None:
        """
        Parsea un mensaje raw, lo convierte en MarketTick
        y llama al callback solo si el precio cambió significativamente.
        """
        try:
            data = json.loads(raw_message)

            # Maneja mensajes que vienen como lista
            if isinstance(data, list):
                for item in data:
                    await self._process_message(
                        json.dumps(item), market_id, callback, state, log
                    )
                return

            # Parsea a MarketTick usando el adaptador
            tick = PolymarketAdapter.parse_orderbook_message(market_id, data)

            if tick is None:
                return  # Mensaje ignorado (tipo no relevante o datos inválidos)

            # Filtra ticks sin cambio significativo (evita ruido)
            if state.last_tick and not self._is_significant_change(
                state.last_tick, tick
            ):
                return

            # Actualiza estado
            state.record_message(tick)
            WS_TICKS_RECEIVED.labels(market_id=market_id).inc()

            # Guarda estado en Redis para visibilidad
            await self._redis.set_ws_state(market_id, state)

            log.debug(
                "tick_received",
                yes_price=tick.yes_price,
                spread=tick.spread,
                volume=tick.volume_24h,
            )

            # Llama al callback de la estrategia
            await callback(tick)

        except json.JSONDecodeError as e:
            log.warning("ws_json_decode_error", error=str(e))
        except Exception as e:
            log.error("ws_message_processing_error", error=str(e))

    def _is_significant_change(
        self,
        prev: MarketTick,
        curr: MarketTick,
    ) -> bool:
        """
        Devuelve True si el precio cambió más que MIN_PRICE_CHANGE.
        Evita generar ticks innecesarios cuando el mercado está quieto.
        """
        return abs(curr.yes_price - prev.yes_price) >= MIN_PRICE_CHANGE

    # ------------------------------------------------------------------
    # DETECTOR DE CONEXIÓN COLGADA
    # ------------------------------------------------------------------

    async def _stale_checker(
        self,
        market_id: str,
        state:     WSMarketState,
        ws,
        log,
    ) -> None:
        """
        Tarea paralela que verifica si la conexión lleva más de 30s
        sin recibir mensajes. Si es así, cierra el WS para forzar reconexión.
        """
        while True:
            await asyncio.sleep(10)  # Verifica cada 10 segundos

            if state.is_stale(timeout_seconds=PING_TIMEOUT_SECONDS):
                log.warning(
                    "ws_stale_connection",
                    last_message_at=state.last_message_at,
                )
                # Cierra el WS → el loop externo detectará el error y reconectará
                await ws.close()
                break
