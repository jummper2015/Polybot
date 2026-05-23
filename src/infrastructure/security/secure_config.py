# src/infrastructure/security/secure_config.py

import hashlib
import os
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

# Variables que NUNCA deben aparecer en logs
SENSITIVE_KEYS = {
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
    "TELEGRAM_BOT_TOKEN",
    "DATABASE_URL",          # Contiene password
}

# Variables requeridas siempre (paper y real)
REQUIRED_ALWAYS = [
    "DATABASE_URL",
    "REDIS_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TRADING_MODE",
]

# Variables requeridas solo en real trading
REQUIRED_REAL = [
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
    "POLYMARKET_WALLET_ADDRESS",
]


@dataclass(frozen=True)
class SecureConfig:
    """
    Configuración validada y sanitizada.
    Se construye una vez al arranque — inmutable después.
    Nunca expone valores sensibles directamente.
    """
    trading_mode:           str
    database_url:           str
    redis_url:              str
    telegram_chat_id:       int
    paper_initial_balance:  float
    bat_threshold:          float
    bat_required_ticks:     int
    bat_stop_loss_pct:      float
    bat_target_price:       float
    bat_position_size_usdc: float
    risk_min_balance_usdc:  float
    risk_max_drawdown_pct:  float
    risk_max_exposure_pct:  float
    risk_max_positions:     int

    # Fingerprint de la private key (hash) — para verificar sin exponer
    _private_key_fingerprint: str = ""

    @classmethod
    def from_env(cls) -> "SecureConfig":
        """
        Carga, valida y construye SecureConfig desde variables de entorno.
        Lanza EnvironmentError si alguna variable requerida falta o es inválida.
        Falla rápido — mejor en el arranque que durante un trade.
        """
        log = logger.bind(action="secure_config_load")

        # ── Valida variables siempre requeridas ───────────────────────
        missing = [k for k in REQUIRED_ALWAYS if not os.environ.get(k)]
        if missing:
            raise EnvironmentError(
                f"Variables de entorno requeridas no encontradas: {missing}\n"
                f"Revisa tu .env — nunca expongas valores reales aquí."
            )

        trading_mode = os.environ["TRADING_MODE"].strip().lower()
        if trading_mode not in ("paper", "real"):
            raise EnvironmentError(
                f"TRADING_MODE debe ser 'paper' o 'real', got: '{trading_mode}'"
            )

        # ── Valida variables de real trading si aplica ────────────────
        if trading_mode == "real":
            missing_real = [k for k in REQUIRED_REAL if not os.environ.get(k)]
            if missing_real:
                raise EnvironmentError(
                    f"Real trading requiere estas variables no encontradas: "
                    f"{missing_real}"
                )
            log.info("real_trading_env_validated")

        # ── Carga y valida valores numéricos ──────────────────────────
        try:
            config = cls(
                trading_mode=trading_mode,
                database_url=os.environ["DATABASE_URL"],
                redis_url=os.environ["REDIS_URL"],
                telegram_chat_id=int(os.environ["TELEGRAM_CHAT_ID"]),
                paper_initial_balance=float(
                    os.environ.get("PAPER_INITIAL_BALANCE", "1000.0")
                ),
                bat_threshold=float(
                    os.environ.get("BAT_THRESHOLD", "0.75")
                ),
                bat_required_ticks=int(
                    os.environ.get("BAT_REQUIRED_TICKS", "3")
                ),
                bat_stop_loss_pct=float(
                    os.environ.get("BAT_STOP_LOSS_PCT", "0.15")
                ),
                bat_target_price=float(
                    os.environ.get("BAT_TARGET_PRICE", "0.90")
                ),
                bat_position_size_usdc=float(
                    os.environ.get("BAT_POSITION_SIZE_USDC", "10.0")
                ),
                risk_min_balance_usdc=float(
                    os.environ.get("RISK_MIN_BALANCE_USDC", "50.0")
                ),
                risk_max_drawdown_pct=float(
                    os.environ.get("RISK_MAX_DAILY_DRAWDOWN", "0.10")
                ),
                risk_max_exposure_pct=float(
                    os.environ.get("RISK_MAX_EXPOSURE_PCT", "0.30")
                ),
                risk_max_positions=int(
                    os.environ.get("RISK_MAX_OPEN_POSITIONS", "5")
                ),
                _private_key_fingerprint=(
                    cls._fingerprint(os.environ.get("POLYMARKET_PRIVATE_KEY", ""))
                    if trading_mode == "real" else ""
                ),
            )
        except (ValueError, KeyError) as e:
            raise EnvironmentError(
                f"Error al parsear configuración: {e}\n"
                f"Verifica que los valores numéricos en .env sean válidos."
            )

        # ── Valida coherencia de parámetros ───────────────────────────
        config._validate_coherence()

        log.info(
            "secure_config_loaded",
            trading_mode=trading_mode,
            bat_threshold=config.bat_threshold,
            risk_max_positions=config.risk_max_positions,
            # NUNCA loguear database_url, tokens ni keys
        )

        return config

    def _validate_coherence(self) -> None:
        """
        Valida que los parámetros sean coherentes entre sí.
        Mismo nivel de validación que BuyAboveThresholdConfig.validate()
        """
        if not 0.50 <= self.bat_threshold <= 0.95:
            raise EnvironmentError(
                f"BAT_THRESHOLD={self.bat_threshold} debe estar entre 0.50 y 0.95"
            )
        if not self.bat_threshold < self.bat_target_price <= 0.99:
            raise EnvironmentError(
                f"BAT_TARGET_PRICE={self.bat_target_price} debe ser "
                f"> BAT_THRESHOLD={self.bat_threshold}"
            )
        if not 0.0 < self.bat_stop_loss_pct <= 0.50:
            raise EnvironmentError(
                f"BAT_STOP_LOSS_PCT={self.bat_stop_loss_pct} debe estar entre 0 y 0.50"
            )
        if self.paper_initial_balance < 10.0:
            raise EnvironmentError(
                f"PAPER_INITIAL_BALANCE={self.paper_initial_balance} "
                f"debe ser >= 10.0 USDC"
            )

    @staticmethod
    def _fingerprint(value: str) -> str:
        """
        Genera un hash SHA-256 de la private key.
        Permite verificar que la key no cambió sin exponerla.
        Solo los primeros 8 caracteres del hash — suficiente para verificar.
        """
        if not value:
            return ""
        return hashlib.sha256(value.encode()).hexdigest()[:8]

    def safe_repr(self) -> dict:
        """
        Representación segura para logs y health checks.
        Nunca incluye valores sensibles.
        """
        return {
            "trading_mode":           self.trading_mode,
            "bat_threshold":          self.bat_threshold,
            "bat_required_ticks":     self.bat_required_ticks,
            "bat_stop_loss_pct":      self.bat_stop_loss_pct,
            "bat_target_price":       self.bat_target_price,
            "bat_position_size_usdc": self.bat_position_size_usdc,
            "risk_min_balance_usdc":  self.risk_min_balance_usdc,
            "risk_max_drawdown_pct":  self.risk_max_drawdown_pct,
            "risk_max_exposure_pct":  self.risk_max_exposure_pct,
            "risk_max_positions":     self.risk_max_positions,
            "key_fingerprint":        self._private_key_fingerprint or "N/A",
            # database_url, redis_url, tokens: OMITIDOS
        }
