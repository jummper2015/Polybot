# src/infrastructure/polymarket/http_client.py

import httpx
import structlog
from src.application.ports.market_data_port import IMarketDataPort
from src.domain.value_objects.market_tick import MarketTick
from src.infrastructure.polymarket.adapters import PolymarketAdapter
from src.infrastructure.polymarket.ws_client import PolymarketWSClient
from src.infrastructure.observability.metrics import HTTP_REQUEST_DURATION

logger = structlog.get_logger(__name__)

# URLs base de la API REST de Polymarket
CLOB_BASE_URL   = "https://clob.polymarket.com"
GAMMA_BASE_URL  = "https://gamma-api.polymarket.com"


class PolymarketHTTPClient(IMarketDataPort):
    """
    Implementa IMarketDataPort usando la API REST + WS de Polymarket.
    REST para discovery y snapshots. WS para streaming en tiempo real.
    """

    def __init__(self, ws_client: PolymarketWSClient):
        self._ws      = ws_client
        self._adapter = PolymarketAdapter()
        # Cliente HTTP async con timeout configurado
        self._http    = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"Accept": "application/json"},
        )

    # ------------------------------------------------------------------
    # IMarketDataPort — Discovery
    # ------------------------------------------------------------------

    async def get_active_markets(
        self, asset: str, window: str
    ) -> list[dict]:
        """
        Consulta Polymarket Gamma API para obtener mercados activos.
        Filtra por asset en el query param y devuelve dicts normalizados.
        """
        log = logger.bind(asset=asset, window=window)

        try:
            with HTTP_REQUEST_DURATION.labels(
                endpoint="get_active_markets"
            ).time():
                response = await self._http.get(
                    f"{GAMMA_BASE_URL}/markets",
                    params={
                        "active":    "true",
                        "closed":    "false",
                        "tag":       asset,       # Filtra por tag BTC o ETH
                        "_limit":    "100",
                        "_order":    "volume24hr",
                        "_sort":     "desc",
                    },
                )
                response.raise_for_status()

            raw_markets = response.json()

            # Normaliza cada market al formato esperado por MarketService
            normalized = [
                PolymarketAdapter.parse_rest_market(m)
                for m in raw_markets
            ]

            log.info("markets_fetched", count=len(normalized))
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
    # IMarketDataPort — Tick por REST (fallback cuando WS no disponible)
    # ------------------------------------------------------------------

    async def get_market_tick(self, market_id: str) -> MarketTick:
        """
        Obtiene el tick actual de un mercado via REST (CLOB API).
        Usado como fallback si el WS no tiene datos recientes.
        Primero intenta el estado WS en memoria, luego hace REST call.
        """
        # Intenta usar el último tick del WebSocket si está disponible
        ws_state = None
        # Aquí accedemos al ws_client directamente para el fallback
        from src.domain.value_objects.ws_state import WSConnectionStatus
        state = await self._ws.get_state(market_id)

        if (
            state
            and state.status == WSConnectionStatus.CONNECTED
            and state.last_tick
            and not state.is_stale(timeout_seconds=60)
        ):
            return state.last_tick

        # Fallback: REST call al CLOB
        logger.info("ws_fallback_to_rest", market_id=market_id)

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
        """Cierra el cliente HTTP y todas las conexiones WS."""
        await self._ws.unsubscribe_all()
        await self._http.aclose()