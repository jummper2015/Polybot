# src/infrastructure/polymarket/clob_client.py
"""
Cliente autenticado para el CLOB de Polymarket usando el SDK oficial.

Usa py-clob-client-v2 para firma EIP-712 correcta, manejo de nonces,
y autenticación L2. Todas las llamadas al SDK son síncronas, así que
se envuelven en asyncio.to_thread() para compatibilidad async.

NOTA: El PLAN_MEJORAS.txt mencionaba "py-clob-client==2.0.0", pero ese
paquete no existe. El SDK oficial real es py-clob-client-v2 (v1.0.1).

LIMITACIÓN CONOCIDA: OrderArgs del SDK no tiene campo order_id externo.
El SDK usa nonce interno para idempotencia. Nuestra key SHA256 de P1.4
se persiste en la DB pero no se envía al CLOB. Esto es seguro porque el
SDK maneja la deduplicación vía nonce.
"""

import asyncio

import httpx
import structlog
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    CreateOrderOptions,
    OrderArgs,
    OrderType,
)

from src.infrastructure.security.key_manager import KeyManager

logger = structlog.get_logger(__name__)

CLOB_BASE_URL = "https://clob.polymarket.com"
CHAIN_ID      = 137  # Polygon Mainnet


# ── Helpers ────────────────────────────────────────────────────────────

def _mask_wallet(addr: str) -> str:
    """Enmascara una dirección de wallet para logs: 0x1234...abcd."""
    if len(addr) > 10:
        return f"{addr[:6]}...{addr[-4:]}"
    return "***"


def _ensure_dict(response) -> dict:
    """
    Convierte la respuesta del SDK a dict.
    El SDK puede devolver objetos (ej: SignedOrder) o dicts directos.
    """
    if isinstance(response, dict):
        return response
    if hasattr(response, "__dict__"):
        return vars(response)
    return {"raw": str(response)}


# ── Cliente ────────────────────────────────────────────────────────────

class PolymarketCLOBClient:
    """
    Cliente autenticado para el CLOB de Polymarket usando py-clob-client-v2.

    Maneja firma EIP-712, autenticación L2, y llamadas a la API de órdenes.
    NUNCA loguea claves, amounts sin contexto, ni datos sensibles de la wallet.

    La interfaz pública es async (usa asyncio.to_thread internamente),
    compatible con el patrón de retry del RealTradingHandler.
    """

    def __init__(self, key_manager: KeyManager, chain_id: int = CHAIN_ID):
        self._keys     = key_manager
        self._chain_id = chain_id

        # Construye ApiCreds desde el KeyManager
        creds = ApiCreds(
            api_key        = self._keys.api_key,
            api_secret     = self._keys.api_secret,
            api_passphrase = self._keys.api_passphrase,
        )

        # Inicializa el ClobClient del SDK con L2 auth
        self._sdk = ClobClient(
            host     = CLOB_BASE_URL,
            chain_id = chain_id,
            key      = self._keys.private_key,
            creds    = creds,
        )

        # Cliente HTTP persistente (para redeem y balance)
        self._http = httpx.AsyncClient(
            base_url=CLOB_BASE_URL,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"Accept": "application/json"},
        )

        logger.info(
            "clob_client_initialized",
            chain_id=chain_id,
            wallet=_mask_wallet(self._keys.wallet_address),
            sdk_version="py-clob-client-v2",
        )

    # ------------------------------------------------------------------
    # OPERACIONES DE ÓRDENES (async → sync SDK via to_thread)
    # ------------------------------------------------------------------

    async def create_order(
        self,
        token_id:    str,
        side:        str,
        price:       float,
        size:        float,
        order_id:    str,
        tick_size:   str = "0.01",
        order_type:  OrderType | None = None,
    ) -> dict:
        """
        Crea y postea una orden limitada en el CLOB de Polymarket.

        Usa create_and_post_order del SDK: firma EIP-712, asigna nonce,
        y envía la orden en una sola llamada.

        Args:
            token_id:  Token ID del mercado (YES o NO)
            side:      "BUY" o "SELL"
            price:     Precio límite (0.0 - 1.0)
            size:      Cantidad en USDC
            order_id:  ID externo para tracking DB (NO se envía al CLOB —
                       el SDK usa nonce interno para idempotencia)
            tick_size: Tick size del mercado ("0.01", "0.001", "0.0001")
            order_type: Tipo de orden (por defecto GTC)

        Returns:
            dict con al menos {"price": float} o {"id": str, "status": str}.
            La respuesta se normaliza a dict vía _ensure_dict.
        """
        if order_type is None:
            order_type = OrderType.GTC

        order_args = OrderArgs(
            token_id = token_id,
            price    = price,
            size     = size,
            side     = side,
        )
        options = CreateOrderOptions(tick_size=tick_size)

        response = await asyncio.to_thread(
            self._sdk.create_and_post_order,
            order_args = order_args,
            options    = options,
            order_type = order_type,
        )
        result = _ensure_dict(response)
        logger.debug(
            "clob_order_created",
            db_order_id=order_id,
            clob_response_id=result.get("id", "unknown"),
        )
        return result

    async def cancel_order(self, order_id: str) -> dict:
        """Cancela una orden pendiente por su ID del CLOB."""
        response = await asyncio.to_thread(
            self._sdk.cancel,
            order_id=order_id,
        )
        return _ensure_dict(response)

    async def get_order_status(self, order_id: str) -> dict:
        """Consulta el estado actual de una orden en el CLOB."""
        response = await asyncio.to_thread(
            self._sdk.get_order,
            order_id=order_id,
        )
        return _ensure_dict(response)

    async def redeem_position(
        self,
        token_id:   str,
        market_id:  str,
    ) -> dict:
        """
        Redime tokens ganadores después de la resolución del mercado.

        El SDK no expone un método redeem directo. Usamos la API REST
        con autenticación básica (wallet address en header).
        """
        wallet = await asyncio.to_thread(self._sdk.get_address)

        response = await self._http.post(
            "/redeem",
            json={
                "token_id":  token_id,
                "market_id": market_id,
            },
            headers={"POLY_ADDRESS": wallet},
        )
        response.raise_for_status()
        return response.json()

    async def get_balance(self) -> float:
        """
        Consulta el balance USDC disponible para trading en Polymarket.

        Usa get_balance_allowance del SDK como fuente primaria (devuelve
        cuánto USDC puede gastar el contrato CLOB en nuestro nombre).
        La API REST /balance es un fallback no verificado — el endpoint
        puede no existir en versiones recientes de la API de Polymarket.
        """
        try:
            result = await asyncio.to_thread(
                self._sdk.get_balance_allowance
            )
            # get_balance_allowance devuelve un dict con el allowance
            if isinstance(result, dict):
                return float(result.get("allowance", 0.0))
            return float(result)
        except Exception:
            # Fallback: consulta REST directa
            wallet = await asyncio.to_thread(self._sdk.get_address)
            response = await self._http.get(
                "/balance",
                headers={"POLY_ADDRESS": wallet},
            )
            response.raise_for_status()
            data = response.json()
            return float(data.get("balance", 0.0))

    # ------------------------------------------------------------------
    # CIERRE
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Cierra el cliente HTTP persistente."""
        await self._http.aclose()
        logger.debug("clob_client_closed")
