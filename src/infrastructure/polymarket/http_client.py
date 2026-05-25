# src/infrastructure/polymarket/http_client.py

import time

import httpx
import structlog

from src.application.ports.market_data_port import IMarketDataPort
from src.domain.value_objects.market_tick import MarketTick
from src.infrastructure.observability.metrics import (
    HTTP_REQUEST_DURATION,
    MARKET_DATA_SOURCE,
)
from src.infrastructure.polymarket.adapters import PolymarketAdapter
from src.infrastructure.polymarket.ws_client import PolymarketWSClient

logger = structlog.get_logger(__name__)

# URLs base de la API REST de Polymarket
CLOB_BASE_URL   = "https://clob.polymarket.com"
GAMMA_BASE_URL  = "https://gamma-api.polymarket.com"

# Graceful degradation config
REST_CACHE_TTL          = 15.0   # segundos: cachear tick REST para evitar polling excesivo
WS_STALE_THRESHOLD      = 60.0   # segundos: umbral para considerar WS caído
RECOVERY_PROBE_INTERVAL = 30.0   # segundos: cada cuánto probar si WS se recuperó


class PolymarketHTTPClient(IMarketDataPort):
    """
    Implementa IMarketDataPort usando la API REST + WS de Polymarket.
    REST para discovery y snapshots. WS para streaming en tiempo real.

    Graceful degradation WS → REST:
      - Si WS está stale > 60s → activa modo degradado (REST polling 15s cache)
      - En modo degradado, sondea recuperación WS cada 30s
      - Métrica MARKET_DATA_SOURCE rastrea el origen por mercado
    """

    def __init__(self, ws_client: PolymarketWSClient):
        self._ws      = ws_client
        self._adapter = PolymarketAdapter()
        # Cliente HTTP async con timeout configurado
        self._http    = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"Accept": "application/json"},
        )

        # ── Estado de degradación ──────────────────────────────────
        # market_id → timestamp de último tick REST cacheado
        self._rest_cache: dict[str, tuple[float, MarketTick]] = {}
        # market_id → timestamp de cuándo se activó la degradación
        self._degraded_since: dict[str, float] = {}
        # market_id → timestamp del último probe de recuperación
        self._last_recovery_probe: dict[str, float] = {}

    # ------------------------------------------------------------------
    # IMarketDataPort — Discovery
    # ------------------------------------------------------------------

    async def get_active_markets(
        self, asset: str, window: str
    ) -> list[dict]:
        """
        Consulta Polymarket Gamma API /events para obtener mercados activos.

        Usa el endpoint /events (que contiene markets anidados con datos completos:
        conditionId, clobTokenIds, outcomes, outcomePrices, startDateIso, endDateIso)
        en lugar de /markets (que devuelve datos incompletos sin IDs ni tokens).

        Filtra por tag=crypto y luego extrae/flattenea todos los markets de cada evento.
        El filtrado por asset (BTC/ETH) y ventana (5m/15m) se hace en MarketService.
        """
        log = logger.bind(asset=asset, window=window)

        try:
            with HTTP_REQUEST_DURATION.labels(
                endpoint="get_active_markets"
            ).time():
                response = await self._http.get(
                    f"{GAMMA_BASE_URL}/events",
                    params={
                        "active":    "true",
                        "closed":    "false",
                        "tag":       "crypto",
                        "limit":     "50",
                        "order":     "volume24hr",
                        "sort":      "desc",
                    },
                )
                response.raise_for_status()

            events = response.json()

            # Extrae y normaliza todos los markets de todos los eventos
            normalized: list[dict] = []
            for event in events:
                markets = event.get("markets") or []
                for m in markets:
                    try:
                        parsed = PolymarketAdapter.parse_rest_market(m)
                        if parsed.get("condition_id") and parsed.get("tokens"):
                            normalized.append(parsed)
                    except Exception:
                        logger.debug(
                            "market_parse_skipped",
                            market_id=m.get("conditionId", m.get("id", "unknown")),
                            question=str(m.get("question", ""))[:80],
                        )

            log.info("markets_fetched", events=len(events), markets=len(normalized))
            return normalized

        except httpx.HTTPStatusError as e:
            log.error(
                "http_error",
                status_code=e.response.status_code,
                url=str(e.request.url),
            )
            raise
        except httpx.RequestError as e:
            log.error("request_error", error=str(e))
            raise

    # ------------------------------------------------------------------
    # IMarketDataPort — Tick con Graceful Degradation WS → REST
    # ------------------------------------------------------------------

    async def get_market_tick(self, market_id: str) -> MarketTick:
        """
        Obtiene el tick actual de un mercado con degradación progresiva:

        Modo NORMAL (WS):
          1. Usa el último tick del WebSocket si está CONNECTED y no stale.
          2. Si WS está stale > 60s → activa modo DEGRADED.

        Modo DEGRADED (REST):
          3. Cachea el tick REST por 15s para evitar polling excesivo.
          4. Cada 30s sondea si WS se recuperó → vuelve a modo NORMAL.

        La métrica MARKET_DATA_SOURCE se actualiza en cada llamada.
        """
        now = time.monotonic()
        from src.domain.value_objects.ws_state import WSConnectionStatus

        # ── Modo DEGRADED: usar REST con cache ───────────────────────
        if market_id in self._degraded_since:
            return await self._get_tick_degraded(market_id, now)

        # ── Modo NORMAL: intentar WS ─────────────────────────────────
        state = await self._ws.get_state(market_id)

        if (
            state
            and state.status == WSConnectionStatus.CONNECTED
            and state.last_tick
            and not state.is_stale(timeout_seconds=WS_STALE_THRESHOLD)
        ):
            # WS está sano → usar tick directo
            MARKET_DATA_SOURCE.labels(market_id=market_id).set(2.0)
            return state.last_tick

        # ── WS caído o stale → activar degradación ───────────────────
        import datetime as dt
        logger.warning(
            "ws_degraded_activating_rest_fallback",
            market_id=market_id,
            ws_status=state.status.value if state else "no_state",
            last_tick_age_seconds=(
                (dt.datetime.utcnow() - state.last_message_at).total_seconds()
                if state and state.last_message_at else "unknown"
            ),
        )
        self._degraded_since[market_id] = now
        self._last_recovery_probe[market_id] = now
        MARKET_DATA_SOURCE.labels(market_id=market_id).set(1.0)

        return await self._fetch_tick_rest(market_id)

    # ------------------------------------------------------------------
    # MÉTODOS PRIVADOS DE DEGRADACIÓN
    # ------------------------------------------------------------------

    async def _get_tick_degraded(
        self, market_id: str, now: float
    ) -> MarketTick:
        """
        Sirve el tick en modo degradado: cache REST (15s TTL)
        con sondeo periódico de recuperación WS.
        """
        from src.domain.value_objects.ws_state import WSConnectionStatus

        # ── Verificar cache ─────────────────────────────────────────
        cached = self._rest_cache.get(market_id)
        if cached:
            cached_at, tick = cached
            if now - cached_at < REST_CACHE_TTL:
                # Cache hit: devolver tick cacheado sin llamada REST
                MARKET_DATA_SOURCE.labels(market_id=market_id).set(1.0)
                return tick

        # ── Probar recuperación WS periódicamente ───────────────────
        last_probe = self._last_recovery_probe.get(market_id, 0.0)
        if now - last_probe >= RECOVERY_PROBE_INTERVAL:
            self._last_recovery_probe[market_id] = now

            state = await self._ws.get_state(market_id)
            if (
                state
                and state.status == WSConnectionStatus.CONNECTED
                and state.last_tick
                and not state.is_stale(timeout_seconds=WS_STALE_THRESHOLD)
            ):
                # ✅ WS recuperado → salir de degradación
                logger.info(
                    "ws_recovered_from_degraded",
                    market_id=market_id,
                    degraded_seconds=now - self._degraded_since[market_id],
                )
                self._degraded_since.pop(market_id, None)
                self._rest_cache.pop(market_id, None)
                self._last_recovery_probe.pop(market_id, None)
                MARKET_DATA_SOURCE.labels(market_id=market_id).set(2.0)
                return state.last_tick

            logger.debug(
                "ws_still_degraded",
                market_id=market_id,
                ws_status=state.status.value if state else "no_state",
            )

        # ── Cache miss o WS no recuperado → llamada REST ────────────
        tick = await self._fetch_tick_rest(market_id)
        self._rest_cache[market_id] = (now, tick)
        MARKET_DATA_SOURCE.labels(market_id=market_id).set(1.0)
        return tick

    async def _fetch_tick_rest(self, market_id: str) -> MarketTick:
        """
        Llama al endpoint REST /book del CLOB y parsea la respuesta.
        Encapsula la lógica REST para reutilizar desde get_market_tick
        y _get_tick_degraded.
        """
        with HTTP_REQUEST_DURATION.labels(
            endpoint="get_market_tick"
        ).time():
            response = await self._http.get(
                f"{CLOB_BASE_URL}/book",
                params={"token_id": market_id},
            )
            response.raise_for_status()

        raw = response.json()
        raw["event_type"] = "book"  # Fuerza tipo para el adaptador

        tick = PolymarketAdapter.parse_orderbook_message(market_id, raw)
        if not tick:
            raise ValueError(f"No se pudo parsear tick para {market_id}")

        return tick

    # ------------------------------------------------------------------
    # QUERY PÚBLICA: estado de degradación
    # ------------------------------------------------------------------

    def is_degraded(self, market_id: str) -> bool:
        """Devuelve True si el mercado está en modo REST fallback."""
        return market_id in self._degraded_since

    def degraded_seconds(self, market_id: str) -> float | None:
        """Segundos que lleva un mercado en modo degradado, o None si está sano."""
        since = self._degraded_since.get(market_id)
        if since is None:
            return None
        return time.monotonic() - since

    # ------------------------------------------------------------------
    # IMarketDataPort — WebSocket streaming
    # ------------------------------------------------------------------

    async def subscribe_order_book(
        self, market_id: str, callback
    ) -> None:
        """Delega la subscripción WS al cliente WebSocket."""
        await self._ws.subscribe(market_id, callback)

    async def unsubscribe_order_book(self, market_id: str) -> None:
        """Delega la desuscripción al cliente WebSocket."""
        await self._ws.unsubscribe(market_id)

    async def close(self) -> None:
        """Cierra el cliente HTTP, todas las conexiones WS y resetea estado de degradación."""
        await self._ws.unsubscribe_all()
        await self._http.aclose()
        # Limpia estado de degradación
        self._rest_cache.clear()
        self._degraded_since.clear()
        self._last_recovery_probe.clear()
