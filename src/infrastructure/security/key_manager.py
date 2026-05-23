# src/infrastructure/security/key_manager.py

import os
from functools import cached_property

import structlog

logger = structlog.get_logger(__name__)

# Nombres exactos de las variables de entorno esperadas
ENV_PRIVATE_KEY     = "POLYMARKET_PRIVATE_KEY"
ENV_API_KEY         = "POLYMARKET_API_KEY"
ENV_API_SECRET      = "POLYMARKET_API_SECRET"
ENV_API_PASSPHRASE  = "POLYMARKET_API_PASSPHRASE"
ENV_WALLET_ADDRESS  = "POLYMARKET_WALLET_ADDRESS"


class KeyManager:
    """
    Gestiona las claves de acceso a Polymarket de forma segura.

    REGLAS DE SEGURIDAD (inamovibles):
    1. Las claves NUNCA se loguean ni se devuelven como string plano
    2. Se cargan una sola vez al arrancar y se mantienen en memoria
    3. Si alguna clave falta → excepción en el arranque (fail-fast)
    4. Los métodos devuelven las claves solo cuando son llamados
       por el handler autorizado — nunca se exponen en atributos públicos
    """

    def __init__(self):
        # Valida presencia de todas las variables al instanciar
        self._validate_env()
        logger.info(
            "key_manager_initialized",
            wallet=self._masked_wallet(),
            has_private_key=bool(os.environ.get(ENV_PRIVATE_KEY)),
            has_api_key=bool(os.environ.get(ENV_API_KEY)),
        )

    # ------------------------------------------------------------------
    # ACCESO A CLAVES (solo para uso interno del RealTradingHandler)
    # ------------------------------------------------------------------

    @cached_property
    def private_key(self) -> str:
        """
        Clave privada de la wallet.
        `cached_property`: se lee del env solo la primera vez.
        NUNCA loguear el valor retornado.
        """
        return os.environ[ENV_PRIVATE_KEY]

    @cached_property
    def api_key(self) -> str:
        """API Key derivada para autenticación L2 en el CLOB."""
        return os.environ[ENV_API_KEY]

    @cached_property
    def api_secret(self) -> str:
        """Secret para firmar requests al CLOB."""
        return os.environ[ENV_API_SECRET]

    @cached_property
    def api_passphrase(self) -> str:
        """Passphrase adicional requerida por el CLOB de Polymarket."""
        return os.environ[ENV_API_PASSPHRASE]

    @cached_property
    def wallet_address(self) -> str:
        """Dirección pública de la wallet (segura para loguear)."""
        return os.environ[ENV_WALLET_ADDRESS]

    # ------------------------------------------------------------------
    # VALIDACIÓN Y UTILIDADES
    # ------------------------------------------------------------------

    def _validate_env(self) -> None:
        """
        Verifica que todas las variables de entorno necesarias existan.
        Lanza EnvironmentError en el arranque si alguna falta.
        Falla rápido — mejor que fallar en medio de un trade real.
        """
        required = [
            ENV_PRIVATE_KEY,
            ENV_API_KEY,
            ENV_API_SECRET,
            ENV_API_PASSPHRASE,
            ENV_WALLET_ADDRESS,
        ]
        missing = [var for var in required if not os.environ.get(var)]

        if missing:
            raise EnvironmentError(
                f"Variables de entorno requeridas para Real Trading no encontradas: "
                f"{missing}. "
                f"Revisa tu archivo .env y nunca expongas estos valores en logs."
            )

    def _masked_wallet(self) -> str:
        """
        Devuelve la dirección de wallet con el medio oculto.
        Ej: 0x1234...abcd → seguro para logs.
        """
        addr = os.environ.get(ENV_WALLET_ADDRESS, "")
        if len(addr) > 10:
            return f"{addr[:6]}...{addr[-4:]}"
        return "***"

    def is_real_trading_ready(self) -> bool:
        """
        Verifica que todas las claves están disponibles.
        Usado por el health check y antes de activar real trading.
        """
        try:
            self._validate_env()
            return True
        except EnvironmentError:
            return False
