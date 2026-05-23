# src/infrastructure/security/circuit_breaker.py

import asyncio
import time
from dataclasses import dataclass
from enum import Enum

import structlog

from src.domain.exceptions import CircuitBreakerOpenError
from src.infrastructure.observability.metrics import CIRCUIT_BREAKER_STATE

logger = structlog.get_logger(__name__)


class CircuitState(str, Enum):
    """Estados del circuit breaker."""
    CLOSED     = "closed"      # Operación normal — requests pasan
    OPEN       = "open"        # Circuito abierto — todos los requests se bloquean
    HALF_OPEN  = "half_open"   # Probando recuperación — un request de prueba


@dataclass
class CircuitBreakerConfig:
    """
    Configuración del circuit breaker según PLAN_MEJORAS.txt P1.3.

    failure_threshold: fallos consecutivos para abrir el circuito (5)
    recovery_timeout:  segundos antes de pasar a half-open (60)
    window_seconds:    ventana deslizante para contar fallos (60)
    """
    failure_threshold: int   = 5
    recovery_timeout:  float = 60.0
    window_seconds:     float = 60.0


class CLOBCircuitBreaker:
    """
    Circuit Breaker para llamadas al CLOB de Polymarket.

    Implementación custom async-compatible (la librería 'circuitbreaker' de PyPI
    es solo síncrona/decorator-based y no funciona con llamadas async/await).

    Protege contra fallos en cascada cuando la API está caída:
    - 5 fallos en ventana de 60s → circuito ABIERTO
    - Circuito abierto 60s → HALF-OPEN (un request de prueba)
    - Request exitoso en half-open → CIERRA el circuito
    - Request fallido en half-open → RE-ABRE el circuito

    Thread-safe para uso asíncrono (asyncio.Lock).

    Uso:
        breaker = CLOBCircuitBreaker()
        try:
            result = await breaker.call(my_async_fn, arg1, arg2)
        except CircuitBreakerOpenError:
            # Circuito abierto — usar fallback
            ...
    """

    def __init__(self, config: CircuitBreakerConfig | None = None):
        self._config = config or CircuitBreakerConfig()
        self._state:          CircuitState = CircuitState.CLOSED
        self._failure_count:  int = 0
        self._failure_window: list[float] = []  # Timestamps de fallos recientes
        self._opened_at:      float = 0.0
        self._lock:           asyncio.Lock = asyncio.Lock()

        logger.info(
            "circuit_breaker_initialized",
            failure_threshold=self._config.failure_threshold,
            recovery_timeout=self._config.recovery_timeout,
            window_seconds=self._config.window_seconds,
        )
        CIRCUIT_BREAKER_STATE.set(0)

    # ------------------------------------------------------------------
    # PROPIEDADES PÚBLICAS
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Estado actual del circuit breaker (thread-safe read)."""
        return self._state

    def is_open(self) -> bool:
        """
        Verifica si el circuito está abierto y no ha expirado el timeout.
        Si el timeout expiró, transiciona automáticamente a half-open.
        Debe llamarse ANTES de intentar cualquier request.

        Nota: is_open() es síncrono y no puede usar asyncio.Lock.
        En asyncio no hay concurrencia real (single-threaded event loop),
        así que la mutación de estado aquí es segura sin lock.
        """
        if self._state == CircuitState.CLOSED:
            return False

        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._config.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                CIRCUIT_BREAKER_STATE.set(2)
                logger.info(
                    "circuit_half_open",
                    elapsed_seconds=round(elapsed, 1),
                )
                return False
            return True

        # HALF_OPEN: permitir el request de prueba
        return False

    # ------------------------------------------------------------------
    # MÉTODOS PÚBLICOS DE REGISTRO (para uso externo como en retry loops)
    # ------------------------------------------------------------------

    async def record_success(self) -> None:
        """
        Registra un éxito externamente.
        Útil cuando el circuit breaker no envuelve directamente la llamada
        (ej: dentro de un retry loop donde el éxito llega después de varios intentos).
        """
        await self._on_success()

    async def record_failure(self) -> None:
        """
        Registra un fallo externamente.
        Útil cuando el circuit breaker no envuelve directamente la llamada
        (ej: cuando todos los retries se agotan).
        """
        await self._on_failure()

    # ------------------------------------------------------------------
    # MÉTODO PRINCIPAL: ENVOLTURA DE LLAMADAS
    # ------------------------------------------------------------------

    async def call(self, fn, *args, **kwargs):
        """
        Ejecuta fn(*args, **kwargs) con protección de circuit breaker.

        Si el circuito está ABIERTO → lanza CircuitBreakerOpenError.
        Si HALF_OPEN → ejecuta fn (request de prueba).
        Si CLOSED → ejecuta fn normalmente.

        Raises:
            CircuitBreakerOpenError: si el circuito está abierto.
            Exception: re-lanza cualquier excepción de fn.
        """
        if self.is_open():
            CIRCUIT_BREAKER_STATE.set(1)
            raise CircuitBreakerOpenError(
                f"Circuit breaker open for {self._config.recovery_timeout}s "
                f"after {self._failure_count} failures"
            )

        try:
            result = await fn(*args, **kwargs)
            await self._on_success()
            return result
        except Exception:
            await self._on_failure()
            raise

    # ------------------------------------------------------------------
    # INTERNOS
    # ------------------------------------------------------------------

    async def _on_success(self) -> None:
        """Registra un éxito: resetea el circuito a CLOSED."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info("circuit_closed_after_success")
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._failure_window.clear()
            CIRCUIT_BREAKER_STATE.set(0)

    async def _on_failure(self) -> None:
        """Registra un fallo: incrementa contador, puede abrir el circuito."""
        now = time.monotonic()
        async with self._lock:
            # Limpiar fallos fuera de la ventana
            cutoff = now - self._config.window_seconds
            self._failure_window = [
                ts for ts in self._failure_window if ts > cutoff
            ]
            self._failure_window.append(now)
            self._failure_count = len(self._failure_window)

            if (
                self._failure_count >= self._config.failure_threshold
                and self._state != CircuitState.OPEN
            ):
                self._state = CircuitState.OPEN
                self._opened_at = now
                CIRCUIT_BREAKER_STATE.set(1)
                logger.error(
                    "circuit_opened",
                    failure_count=self._failure_count,
                    threshold=self._config.failure_threshold,
                    recovery_timeout=self._config.recovery_timeout,
                )
