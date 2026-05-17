# src/infrastructure/polymarket/clob_client.py

import httpx
import structlog
import asyncio
from datetime import datetime, timezone

from src.infrastructure.security.key_manager import KeyManager

logger = structlog.get_logger(__name__)

CLOB_BASE_URL = "https://clob.polymarket.com"


class PolymarketCLOBClient:
    """
    Cliente autenticado para el CLOB (Central Limit Order Book) de Polymarket.
    Maneja autenticación L1+L2, firma de requests y llamadas a la API de órdenes.
    NUNCA loguea claves, amounts sin contexto, ni datos sensibles de la wallet.
    """

    def __init__(self, key_manager: KeyManager):
        self._keys = key_manager
        self._http = httpx.AsyncClient(
            base_url=CLOB_BASE_URL,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"Accept": "application/json"},
        )

    # ------------------------------------------------------------------
    # AUTENTICACIÓN
    # ------------------------------------------------------------------

    def _get_auth_headers(self, timestamp: str, signature: str) -> dict:
        """
        Construye los headers de autenticación L2 para el CLOB.
        Nunca incluye la private key — solo la API key y la firma.
        """
        return {
            "POLY_ADDRESS":    self._keys.wallet_address,
            "POLY_SIGNATURE":  signature,
            "POLY-API-KEY":    self._keys.api_key,
            "POLY-PASSPHRASE": self._keys.api_passphrase,
            "POLY-TIMESTAMP":  timestamp,
        }

    def _sign_request(self, body: dict) -> tuple[str, str]:
        """
        Firma el request con la private key usando el esquema de Polymarket.
        Devuelve (timestamp, signature) — nunca expone la key.

        NOTA: En implementación real se usa py-clob-client de Polymarket.
        Aquí mostramos la interfaz — la firma real usa EIP-712.
        """
        import time
        import json
        import hmac
        import hashlib

        timestamp = str(int(time.time()))
        body_str  = json.dumps(body, separators=(",", ":"), sort_keys=True)

        # HMAC-SHA256 con el api_secret como clave
        # En producción real: usar py-clob-client que maneja EIP-712
        signature = hmac.new(
            self._keys.api_secret.encode(),
            f"{timestamp}{body_str}".encode(),
            hashlib.sha256,
        ).hexdigest()

        return timestamp, signature

    # ------------------------------------------------------------------
    # OPERACIONES DE ÓRDENES
    # ------------------------------------------------------------------

    async def create_order(
        self,
        token_id:    str,       # YES o NO token ID del mercado
        side:        str,       # "BUY"
        price:       float,     # Precio límite (0.0 - 1.0)
        size:        float,     # Cantidad en USDC
        order_id:    str,       # UUID generado por nosotros (idempotencia)
    ) -> dict:
        """
        Crea una orden limitada en el CLOB de Polymarket.
        Usa el order_id externo para idempotencia.
        """
        body = {
            "token_id":  token_id,
            "side":      side,
            "price":     str(round(price, 4)),
            "size":      str(round(size, 2)),
            "order_id":  order_id,         # Idempotencia: mismo UUID = misma orden
            "order_type": "LIMIT",
            "time_in_force": "FOK",        # Fill or Kill — no órdenes parciales colgadas
        }

        timestamp, signature = self._sign_request(body)
        headers = self._get_auth_headers(timestamp, signature)

        response = await self._http.post(
            "/order",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def cancel_order(self, order_id: str) -> dict:
        """Cancela una orden pendiente por su ID."""
        body      = {"order_id": order_id}
        timestamp, signature = self._sign_request(body)
        headers   = self._get_auth_headers(timestamp, signature)

        response = await self._http.delete(
            f"/order/{order_id}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def get_order_status(self, order_id: str) -> dict:
        """Consulta el estado actual de una orden."""
        response = await self._http.get(f"/order/{order_id}")
        response.raise_for_status()
        return response.json()

    async def redeem_position(
        self,
        token_id:   str,
        market_id:  str,
    ) -> dict:
        """
        Redime tokens ganadores después de que el mercado se resuelve.
        Solo tiene efecto si el mercado está resuelto y ganamos.
        """
        body = {
            "token_id":  token_id,
            "market_id": market_id,
        }
        timestamp, signature = self._sign_request(body)
        headers = self._get_auth_headers(timestamp, signature)

        response = await self._http.post(
            "/redeem",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def get_balance(self) -> float:
        """
        Consulta el balance USDC disponible en la wallet para trading.
        """
        response = await self._http.get(
            "/balance",
            headers={"POLY_ADDRESS": self._keys.wallet_address},
        )
        response.raise_for_status()
        data = response.json()
        return float(data.get("balance", 0.0))

    async def close(self) -> None:
        """Cierra el cliente HTTP."""
        await self._http.aclose()