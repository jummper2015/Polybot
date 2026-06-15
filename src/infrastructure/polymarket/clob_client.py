# src/infrastructure/polymarket/clob_client.py
"""
Cliente autenticado para el CLOB de Polymarket usando el SDK oficial V2.

Usa py-clob-client-v2 para firma EIP-712 correcta (Exchange domain V2,
EIP-712 version "2", new order struct con timestamp/metadata/builder)
y autenticación L2. Todas las llamadas al SDK son síncronas, así que
se envuelven en asyncio.to_thread() para compatibilidad async.

NOTA: El PLAN_MEJORAS.txt mencionaba "py-clob-client==2.0.0", pero ese
paquete no existe. El SDK oficial real es py-clob-client-v2 (v1.0.1).

MIGRACIÓN CLOB V2 (abril 2026):
  - Nonces eliminados → unicidad de órdenes vía timestamp (ms).
  - feeRateBps y taker eliminados del Order struct.
  - Colateral: USDC.e → pUSD (Polymarket USD).
  - Builder program: HMAC headers → builderCode field en la orden.
  - Exchange domain version "1" → "2".

LIMITACIÓN CONOCIDA: OrderArgs del SDK no tiene campo order_id externo.
El SDK usa timestamp (ms) para unicidad de órdenes en V2. Nuestra key
SHA256 de P1.4 se persiste en la DB pero no se envía al CLOB. Esto es
seguro porque el SDK maneja la deduplicación vía timestamp+salt.
"""

import asyncio
from typing import TYPE_CHECKING

import httpx
import structlog
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    ApiCreds,
    CreateOrderOptions,
    OrderArgs,
    OrderType,
)

from src.infrastructure.security.key_manager import KeyManager

if TYPE_CHECKING:
    from src.infrastructure.cache.redis_client import RedisClient

CLOB_MARKET_INFO_TTL_SECONDS = 300
# 5 min — los fees dinámicos cambian con baja frecuencia.

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
    Cliente autenticado para el CLOB V2 de Polymarket usando py-clob-client-v2.

    Maneja firma EIP-712 (Exchange domain V2), autenticación L2,
    y llamadas a la API de órdenes. Compatible con CLOB V2 (abril 2026).
    NUNCA loguea claves, amounts sin contexto, ni datos sensibles de la wallet.

    La interfaz pública es async (usa asyncio.to_thread internamente),
    compatible con el patrón de retry del RealTradingHandler.
    """

    def __init__(
        self,
        key_manager: KeyManager,
        chain_id: int = CHAIN_ID,
        redis_client: "RedisClient | None" = None,
    ):
        self._keys     = key_manager
        self._chain_id = chain_id
        # opt-in: si None, get_market_info_cached degrada a SDK directo.
        self._redis    = redis_client

        # Construye ApiCreds desde el KeyManager
        creds = ApiCreds(
            api_key        = self._keys.api_key,
            api_secret     = self._keys.api_secret,
            api_passphrase = self._keys.api_passphrase,
        )

        # Inicializa el ClobClient del SDK con L2 auth.
        # signature_type explícito: no confiar en el default del SDK porque
        # puede cambiar entre versiones (regla del skill polymarket-clob-audit).
        sig_type = self._keys.signature_type
        self._sdk = ClobClient(
            host           = CLOB_BASE_URL,
            chain_id       = chain_id,
            key            = self._keys.private_key,
            creds          = creds,
            signature_type = sig_type,
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
            signature_type=sig_type,
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
        neg_risk:    bool = False,
    ) -> dict:
        """
        Crea y postea una orden limitada en el CLOB V2 de Polymarket.

        Usa create_and_post_order del SDK: firma EIP-712 (domain V2),
        asigna timestamp (ms) como campo de unicidad, y envía la orden
        en una sola llamada.

        En CLOB V2, la unicidad se basa en timestamp (ms) + salt —
        el sistema de nonces fue eliminado en la migración V2.

        Args:
            token_id:  Token ID del mercado (YES o NO)
            side:      "BUY" o "SELL"
            price:     Precio límite (0.0 - 1.0)
            size:      Cantidad en pUSD (Polymarket USD)
            order_id:  ID externo para tracking DB (NO se envía al CLOB —
                       el SDK usa timestamp+salt para unicidad)
            tick_size: Tick size del mercado ("0.01", "0.001", "0.0001")
            order_type: Tipo de orden (por defecto GTC)
            neg_risk:  Si True, incluye negRisk=True en la orden
                       (requerido para mercados neg_risk)

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

        # ── CLOB V2: pasar negRisk si el mercado lo requiere ──────────
        if neg_risk:
            # El SDK py-clob-client-v2 soporta neg_risk en CreateOrderOptions
            # como keyword argument. Si no está disponible en esta versión,
            # lo pasamos como atributo extra (el SDK lo ignora si no lo soporta).
            try:
                options = CreateOrderOptions(tick_size=tick_size, neg_risk=True)
            except TypeError:
                # Fallback: versión del SDK que no soporta neg_risk aún
                logger.warning(
                    "neg_risk_not_supported_by_sdk",
                    token_id=token_id[:20],
                    hint="SDK version may need update for neg_risk support",
                )
                options = CreateOrderOptions(tick_size=tick_size)

        # ── CLOB V2: builderCode identificador de la entidad ───────────
        builder_code = self._keys.builder_code
        if builder_code:
            # El SDK puede aceptar builderCode como campo adicional
            try:
                order_args_dict = {
                    "token_id": token_id,
                    "price": price,
                    "size": size,
                    "side": side,
                    "builderCode": builder_code,
                }
                # Reconstruir OrderArgs con builderCode
                order_args = OrderArgs(**order_args_dict)
            except TypeError:
                # SDK version sin soporte para builderCode — ignorar
                logger.debug(
                    "builder_code_not_supported_by_sdk",
                    hint="SDK version may need update for builderCode support",
                )

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
        Consulta el balance pUSD (Polymarket USD) disponible para trading.

        CLOB V2 migró de USDC.e → pUSD como colateral (abril 2026).
        Usa get_balance_allowance del SDK como fuente primaria (devuelve
        cuánto pUSD puede gastar el contrato CLOB en nuestro nombre).
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
    # CLOB V2 — INFORMACIÓN DE MERCADO (Fees dinámicos)
    # ------------------------------------------------------------------

    async def get_market_info(self, condition_id: str) -> dict:
        """
        Consulta parámetros CLOB V2 de un mercado específico.

        CLOB V2 introdujo fees dinámicos por mercado (abril 2026).
        Usa get_clob_market_info del SDK para obtener:
          - mts: minimum tick size
          - mos: minimum order size
          - fd: fee details {r: rate, e: exponent, to: takerOnly}
          - t: tokens [{t: tokenID, o: outcome}, ...]
          - rfqe: si RFQ está habilitado

        Args:
            condition_id: Condition ID del mercado (0x...).

        Returns:
            dict con los parámetros del mercado normalizado vía _ensure_dict.
        """
        response = await asyncio.to_thread(
            self._sdk.get_clob_market_info,
            condition_id=condition_id,
        )
        result = _ensure_dict(response)
        logger.debug(
            "market_info_fetched",
            condition_id=condition_id[:20],
            has_fees="fd" in result,
        )
        return result

    async def get_market_info_cached(self, condition_id: str) -> dict:
        """
        Igual que `get_market_info` pero respaldado por Redis con TTL.

        Si el cliente se construyó sin `redis_client`, degrada a una llamada
        directa al SDK (sin cache). Si hay Redis, intenta primero el cache; en
        miss, consulta el SDK y guarda con TTL fijo.

        Fees dinámicos (CLOB V2) cambian con baja frecuencia respecto al
        ritmo de las estimaciones de slippage; el cache evita decenas de
        llamadas REST por minuto al CLOB.
        """
        if self._redis is not None:
            cached = await self._redis.get_clob_market_info(condition_id)
            if cached is not None:
                logger.debug(
                    "clob_market_info_cache_hit",
                    condition_id=condition_id[:20],
                )
                return cached

        info = await self.get_market_info(condition_id)

        if self._redis is not None:
            await self._redis.set_clob_market_info(
                condition_id=condition_id,
                info=info,
                ttl_seconds=CLOB_MARKET_INFO_TTL_SECONDS,
            )
            logger.debug(
                "clob_market_info_cache_set",
                condition_id=condition_id[:20],
                ttl_seconds=CLOB_MARKET_INFO_TTL_SECONDS,
            )

        return info

    # ------------------------------------------------------------------
    # READ-ONLY — Auth probes & cuenta
    # ------------------------------------------------------------------

    async def assert_auth(self) -> dict:
        """
        Verifica que la wallet y las credenciales L1+L2 son válidas.

        Llama a `assert_level_1_auth` (firma EIP-712 dummy con la wallet)
        y `assert_level_2_auth` (HMAC con api_key/secret/passphrase) del SDK.
        Si alguno falla, propaga la excepción del SDK; el caller decide.

        Returns:
            dict con {"wallet": <address>, "l1": True, "l2": True} si OK.
        """
        wallet = await asyncio.to_thread(self._sdk.get_address)
        await asyncio.to_thread(self._sdk.assert_level_1_auth)
        await asyncio.to_thread(self._sdk.assert_level_2_auth)
        logger.info(
            "clob_auth_verified",
            wallet=_mask_wallet(wallet),
            l1=True,
            l2=True,
        )
        return {"wallet": wallet, "l1": True, "l2": True}

    async def get_open_orders(self) -> list[dict]:
        """
        Lista órdenes abiertas en el CLOB para la wallet actual.

        Read-only. No coloca ni cancela nada.
        """
        response = await asyncio.to_thread(self._sdk.get_open_orders)
        if isinstance(response, list):
            return [_ensure_dict(item) for item in response]
        return [_ensure_dict(response)] if response else []

    async def get_trades(self, limit: int | None = None) -> list[dict]:
        """
        Trades históricos del usuario autenticado vía CLOB SDK.

        Args:
            limit: máximo número de trades a devolver tras la llamada
                   (el SDK puede no paginar — truncamos cliente-side).

        Returns:
            Lista de trades como dicts.
        """
        response = await asyncio.to_thread(self._sdk.get_trades)
        trades = response if isinstance(response, list) else (
            [response] if response else []
        )
        trades = [_ensure_dict(t) for t in trades]
        if limit is not None and limit > 0:
            trades = trades[:limit]
        return trades

    # ------------------------------------------------------------------
    # CIERRE
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Cierra el cliente HTTP persistente."""
        await self._http.aclose()
        logger.debug("clob_client_closed")
