# src/interfaces/telegram/pin_gate.py
"""
Ola 2.2: PinGate — Capa 2 de las 3 capas de confirmación para real trading.

Contrato (paper-vs-real-execution skill):
  Capa 1: RiskEngine.evaluate()
  Capa 2: Telegram PIN de 6 dígitos + rate limit 3 intentos → bloqueo 10 min
  Capa 3: Idempotency key

Este módulo implementa Capa 2. Diseño:

  - PIN esperado: SHA256 del PIN de 6 dígitos (env REAL_MODE_PIN_HASH).
    Sin hash en el .env → hasheamos runtime (dev only, warning log).
  - Rate limit por chat_id: contador de fallos consecutivos en memoria.
    3 fallos → bloqueo por LOCKOUT_SECONDS.
  - Comparación constant-time (hmac.compare_digest) para evitar
    side-channel timing attacks.
  - Todo intento (éxito, fallo, bloqueado) genera audit log.
  - No persiste en DB — process-restart resetea contadores (por diseño:
    los operadores confían en el process para blast radius, y un
    restart intencionado no debe seguir bloqueando).
"""

import hashlib
import hmac
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)

# Ola 2.2: 3 fallos consecutivos → bloqueo 10 min (spec RUTA 2.2).
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_SECONDS     = 600  # 10 minutos

# Formato del PIN: exactamente 6 dígitos (0-9).
PIN_REGEX = re.compile(r"^\d{6}$")


class PinResult(str, Enum):
    """Resultados posibles de PinGate.verify."""
    OK              = "ok"
    INVALID_FORMAT  = "invalid_format"
    WRONG           = "wrong"
    LOCKED_OUT      = "locked_out"
    NOT_CONFIGURED  = "not_configured"


@dataclass
class _AttemptState:
    """Estado por chat_id: contadores + timestamp de bloqueo."""
    failed_count:  int   = 0
    locked_until:  float = 0.0  # UNIX ts; 0 = no bloqueado


@dataclass
class PinGate:
    """
    Verificador de PIN con rate-limit por chat_id. Instancia única
    en el proceso — se comparte entre callbacks del bot.
    """
    _expected_hash: str                        = ""
    _attempts:      dict[int, _AttemptState]   = field(default_factory=dict)
    # Config override para tests.
    _lockout_seconds: int                      = LOCKOUT_SECONDS
    _max_failed:      int                      = MAX_FAILED_ATTEMPTS

    @classmethod
    def from_env(
        cls,
        env_key_hash:  str = "REAL_MODE_PIN_HASH",
        env_key_plain: str = "REAL_MODE_PIN",
    ) -> "PinGate":
        """
        Construye el gate leyendo del entorno:
          - Preferido: REAL_MODE_PIN_HASH = SHA256(pin).hexdigest()
          - Fallback dev: REAL_MODE_PIN = pin plano (6 dígitos), se hashea
            en memoria (con warning). Nunca guardado a disco.
          - Si ninguno está, el gate queda NOT_CONFIGURED y todo verify()
            retorna NOT_CONFIGURED (real trading queda bloqueado por diseño).
        """
        hex_hash = os.environ.get(env_key_hash, "").strip().lower()
        if hex_hash:
            if not re.fullmatch(r"[0-9a-f]{64}", hex_hash):
                logger.error(
                    "pin_gate_hash_malformed",
                    reason="REAL_MODE_PIN_HASH debe ser SHA256 hex (64 chars)",
                )
                return cls(_expected_hash="")
            return cls(_expected_hash=hex_hash)

        plain = os.environ.get(env_key_plain, "").strip()
        if plain:
            if not PIN_REGEX.match(plain):
                logger.error(
                    "pin_gate_plain_malformed",
                    reason="REAL_MODE_PIN debe ser 6 dígitos",
                )
                return cls(_expected_hash="")
            logger.warning(
                "pin_gate_using_plain_pin",
                recommendation="Configurar REAL_MODE_PIN_HASH (SHA256) en producción",
            )
            return cls(_expected_hash=hashlib.sha256(plain.encode()).hexdigest())

        logger.warning(
            "pin_gate_not_configured",
            note="Real trading queda bloqueado hasta configurar REAL_MODE_PIN_HASH",
        )
        return cls(_expected_hash="")

    def is_configured(self) -> bool:
        """True si hay un hash esperado válido."""
        return bool(self._expected_hash)

    def is_locked(self, chat_id: int, now: float | None = None) -> bool:
        """
        True si `chat_id` está bloqueado por rate-limit. Efecto colateral:
        si el bloqueo expiró, resetea `failed_count` y `locked_until`.
        """
        now = now if now is not None else time.time()
        st = self._attempts.get(chat_id)
        if st is None:
            return False
        if st.locked_until > now:
            return True
        if st.locked_until > 0:
            # Bloqueo expirado — reset ventana.
            st.failed_count  = 0
            st.locked_until  = 0.0
        return False

    def verify(
        self,
        chat_id: int,
        pin:     str,
        now:     float | None = None,
    ) -> PinResult:
        """
        Verifica el PIN aportado por `chat_id`. Único punto de verdad.

        Retornos:
          - OK: PIN correcto; resetea contadores del chat_id.
          - INVALID_FORMAT: no son 6 dígitos; NO cuenta como intento
            (sigue estando protegido pero no gastas un slot en typos
            obvios). Rechazo silencioso a nivel de UX; el UI muestra
            "formato inválido".
          - WRONG: PIN erróneo; incrementa failed_count. Si supera
            MAX_FAILED_ATTEMPTS → LOCKED_OUT en la siguiente llamada.
          - LOCKED_OUT: chat_id bloqueado; no verifica el PIN.
          - NOT_CONFIGURED: gate sin PIN cargado; nunca da OK.
        """
        now = now if now is not None else time.time()
        log = logger.bind(chat_id=chat_id, action="pin_verify")

        if not self.is_configured():
            log.warning("pin_gate_not_configured_verify")
            return PinResult.NOT_CONFIGURED

        if self.is_locked(chat_id, now=now):
            log.warning(
                "pin_gate_locked_out",
                unlocks_in_seconds=int(self._attempts[chat_id].locked_until - now),
            )
            return PinResult.LOCKED_OUT

        if not PIN_REGEX.match(pin or ""):
            # No cuenta como intento — solo señalamos formato.
            log.info("pin_gate_invalid_format")
            return PinResult.INVALID_FORMAT

        received_hash = hashlib.sha256(pin.encode()).hexdigest()
        if hmac.compare_digest(received_hash, self._expected_hash):
            # OK — reset del contador del chat_id.
            self._attempts.pop(chat_id, None)
            log.info("pin_gate_ok")
            return PinResult.OK

        # Wrong PIN — cuenta el intento.
        st = self._attempts.setdefault(chat_id, _AttemptState())
        st.failed_count += 1
        log.warning("pin_gate_wrong", failed_count=st.failed_count)

        if st.failed_count >= self._max_failed:
            st.locked_until = now + self._lockout_seconds
            log.warning(
                "pin_gate_lockout_triggered",
                lockout_seconds=self._lockout_seconds,
            )
        return PinResult.WRONG

    def seconds_until_unlock(
        self, chat_id: int, now: float | None = None
    ) -> int:
        """
        Segundos restantes del lockout (0 si no bloqueado). Útil para
        formatear el mensaje "vuelve a intentar en X min" en el UI.
        """
        now = now if now is not None else time.time()
        st = self._attempts.get(chat_id)
        if not st or st.locked_until <= now:
            return 0
        return int(st.locked_until - now)
