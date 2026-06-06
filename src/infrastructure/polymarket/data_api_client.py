# src/infrastructure/polymarket/data_api_client.py
"""
Cliente para la Data API pública de Polymarket.

La Data API es un endpoint público (no requiere autenticación L2 ni API keys)
que expone datos de posiciones, trades históricos, y actividad de usuarios.

Endpoint principal usado:
  GET https://data-api.polymarket.com/positions?user=<wallet_address>

La respuesta incluye conditionId, size, avgPrice, currentValue, cashPnl, etc.
para cada posición activa de la wallet especificada.

Usado para verificación cruzada en el health check: comparar posiciones
locales (DB) vs las que Polymarket registra como activas (Data API).
"""

import httpx
import structlog

from src.infrastructure.observability.metrics import HTTP_REQUEST_DURATION

logger = structlog.get_logger(__name__)

DATA_API_BASE_URL = "https://data-api.polymarket.com"


# ── Helpers ────────────────────────────────────────────────────────────

def _mask_wallet(addr: str) -> str:
    """Enmascara una dirección de wallet para logs: 0x1234...abcd."""
    if len(addr) > 10:
        return f"{addr[:6]}...{addr[-4:]}"
    return "***"


# ── Cliente ────────────────────────────────────────────────────────────

class DataAPIClient:
    """
    Cliente para la Data API pública de Polymarket.

    No requiere autenticación — la Data API es un endpoint público.
    Solo necesita la wallet address como parámetro de query.

    Usa httpx.AsyncClient con timeout configurado para no bloquear
    el health check en caso de latencia de red.
    """

    def __init__(self, wallet_address: str):
        self._wallet = wallet_address

        self._http = httpx.AsyncClient(
            base_url=DATA_API_BASE_URL,
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"Accept": "application/json"},
        )

        logger.info(
            "data_api_client_initialized",
            wallet=_mask_wallet(wallet_address),
        )

    # ------------------------------------------------------------------
    # POSICIONES
    # ------------------------------------------------------------------

    async def get_positions(
        self,
        condition_ids: list[str] | None = None,
    ) -> list[dict]:
        """
        Consulta posiciones activas desde la Data API de Polymarket.

        Endpoint: GET /positions?user=<wallet>[&market=id1,id2,...]

        La respuesta es un array de Position objects con campos:
          - conditionId:  str   — ID del mercado
          - asset:        str   — dirección del token
          - size:         float — número de shares
          - avgPrice:     float — precio promedio de compra
          - initialValue: float — valor inicial en pUSD
          - currentValue: float — valor actual en pUSD
          - cashPnl:      float — PnL en pUSD
          - percentPnl:   float — PnL porcentual
          - curPrice:     float — precio actual
          - redeemable:   bool  — si es redimible
          - title:        str   — título del mercado
          - outcome:      str   — YES/NO
          - endDate:      str   — fecha de cierre ISO

        Args:
            condition_ids: Lista opcional de condition IDs para filtrar.
                           Si es None, devuelve todas las posiciones.

        Returns:
            Lista de dicts con los datos de posición de la Data API.
        """
        params: dict = {"user": self._wallet}

        if condition_ids:
            params["market"] = ",".join(condition_ids)

        try:
            with HTTP_REQUEST_DURATION.labels(
                endpoint="data_api_get_positions"
            ).time():
                response = await self._http.get(
                    "/positions",
                    params=params,
                )
                response.raise_for_status()

            positions = response.json()

            logger.debug(
                "data_api_positions_fetched",
                wallet=_mask_wallet(self._wallet),
                count=len(positions) if isinstance(positions, list) else 0,
            )

            return positions if isinstance(positions, list) else []

        except httpx.HTTPStatusError as e:
            logger.warning(
                "data_api_positions_http_error",
                status=e.response.status_code,
                wallet=_mask_wallet(self._wallet),
            )
            raise
        except httpx.RequestError as e:
            logger.warning(
                "data_api_positions_request_error",
                error=str(e),
                wallet=_mask_wallet(self._wallet),
            )
            raise

    # ------------------------------------------------------------------
    # CIERRE
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Cierra el cliente HTTP persistente."""
        await self._http.aclose()
        logger.debug("data_api_client_closed")
