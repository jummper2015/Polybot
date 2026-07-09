"""
domain/exceptions.py
====================
Excepciones tipadas del dominio del bot algorítmico Polymarket.

Regla de uso:
  - Toda excepción de lógica de negocio hereda de PolyBotError.
  - El código de infraestructura (HTTP, WS, DB) atrapa sus propias
    excepciones nativas y las convierte a estas antes de propagarlas
    hacia la capa de aplicación.
  - Los handlers de Telegram y FastAPI solo ven excepciones de este módulo,
    nunca excepciones crudas de librerías externas.

Jerarquía:
  DomainError (alias: PolyBotError)
  ├── MarketError
  │   ├── NoActiveMarketsError
  │   ├── MarketFilterError
  │   ├── MarketNotFoundError
  │   └── MarketExpiredError
  ├── TradingError
  │   ├── InsufficientBalanceError
  │   ├── PositionNotFoundError
  │   ├── OrderRejectedError
  │   └── SignalRejectedError
  ├── ExecutionError
  │   ├── OrderSubmitError
  │   ├── OrderIdempotencyError
  │   ├── ConfirmationTimeoutError
  │   └── CircuitBreakerOpenError
  ├── RiskError
  │   └── RiskDeniedError
  ├── WebSocketError
  │   ├── WebSocketConnectionError
  │   └── WebSocketMaxRetriesError
  ├── ConfigurationError
  │   ├── InvalidConfigError
  │   └── MissingEnvironmentVariableError
  ├── SecurityError
  │   ├── AuthenticationError
  │   └── RateLimitExceededError
  └── InfrastructureError
      ├── DatabaseConnectionError
      └── RedisConnectionError
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imports solo para type hints — evita dependencias circulares en runtime
    from src.domain.enums.asset import Asset
    from src.domain.enums.window import Window


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class DomainError(Exception):
    """
    Raíz de todas las excepciones de dominio del bot.

    Permite capturar cualquier error de negocio con un solo except:
        except DomainError as e:
            log.error("domain_error", error=str(e), type=type(e).__name__)
    """


# Alias para mantener compatibilidad con código existente
PolyBotError = DomainError


# ---------------------------------------------------------------------------
# Errores de mercado (Market Discovery — D-01, D-02, D-23)
# ---------------------------------------------------------------------------

class MarketError(DomainError):
    """Base para errores relacionados con el ciclo de mercado."""


class NoActiveMarketsError(MarketError):
    """
    No hay mercados BTC/ETH activos para la ventana temporal solicitada.

    Se lanza cuando el filtro de discovery (Paso 2 del skill
    polymarket-market-discovery) no encuentra ningún mercado que
    cumpla los tres criterios simultáneamente:
      1. Activo = BTC o ETH (sobre el campo `question`)
      2. Ventana = 5m o 15m (sobre el campo `question`)
      3. Mercado abierto: end_date > now() y resolved == False

    El MarketTimer debe detener el ciclo al recibir esta excepción
    y reintentarlo en el próximo intervalo programado.

    Ejemplo de uso:
        markets = self._filter_markets(raw_markets)
        if not markets:
            raise NoActiveMarketsError(
                asset=Asset.BTC,
                window=Window.M5,
                total_fetched=len(raw_markets),
            )
    """

    def __init__(
        self,
        asset: "Asset | None" = None,
        window: "Window | None" = None,
        total_fetched: int = 0,
        detail: str = "",
    ) -> None:
        self.asset = asset
        self.window = window
        self.total_fetched = total_fetched
        self.detail = detail

        # Construir mensaje legible para logs y Telegram
        parts = ["No hay mercados activos"]
        if asset:
            parts.append(f"para {asset.value}")
        if window:
            parts.append(f"en ventana {window.value}")
        if total_fetched:
            parts.append(f"(revisados {total_fetched} mercados en total)")
        if detail:
            parts.append(f"— {detail}")

        super().__init__(" ".join(parts))

    @property
    def is_recoverable(self) -> bool:
        """
        True si el error puede resolverse en el próximo ciclo sin
        intervención humana (ej: mercado aún no abierto).
        El MarketTimer usa esta propiedad para decidir si alertar
        al operador vía Telegram.
        """
        return True


class MarketFilterError(MarketError):
    """
    El filtro de activo o ventana produjo un resultado ambiguo o inválido.

    Diferencia con NoActiveMarketsError:
      - NoActiveMarketsError: el filtro funcionó pero no encontró nada.
      - MarketFilterError: el filtro encontró algo pero no puede
        clasificarlo de forma determinista.

    Causas más comunes:
      - El campo `question` contiene "BTC" y "ETH" simultáneamente.
      - El campo `question` contiene "5 minute" y "15 minute" a la vez.
      - Los campos `tokens` están malformados (yes_token_id == no_token_id).
      - El market_id está vacío o es None.

    Esta excepción NO es recuperable automáticamente — requiere revisar
    el mercado específico que la causó. El market_id afectado se loggea
    para facilitar el diagnóstico.

    Ejemplo de uso:
        if raw["tokens"][0]["token_id"] == raw["tokens"][1]["token_id"]:
            raise MarketFilterError(
                market_id=raw["condition_id"],
                reason="yes_token_id y no_token_id son idénticos",
                raw_question=raw.get("question", ""),
            )
    """

    def __init__(
        self,
        market_id: str = "",
        reason: str = "",
        raw_question: str = "",
        detail: str = "",
    ) -> None:
        self.market_id = market_id
        self.reason = reason
        self.raw_question = raw_question
        self.detail = detail

        parts = ["Error al filtrar mercado"]
        if market_id:
            parts.append(f"market_id={market_id[:16]}...")
        if reason:
            parts.append(f"— {reason}")
        if detail:
            parts.append(f"({detail})")

        super().__init__(" ".join(parts))

    @property
    def is_recoverable(self) -> bool:
        """
        False: este mercado específico tiene datos malformados en la API
        de Polymarket. No se resolverá solo. Requiere diagnóstico manual.
        """
        return False


class MarketNotFoundError(MarketError):
    """
    El market_id solicitado no existe en la base de datos local ni en Redis.

    Se lanza cuando un servicio intenta operar sobre un mercado que
    debería existir pero no se encuentra. Diferente de
    NoActiveMarketsError: este error es para un mercado concreto,
    no para el resultado de un filtro.

    Ejemplo de uso:
        market = await self._repo.get_market_by_id(market_id)
        if market is None:
            raise MarketNotFoundError(market_id=market_id)
    """

    def __init__(self, market_id: str = "", detail: str = "") -> None:
        self.market_id = market_id
        self.detail = detail

        parts = [f"Mercado no encontrado: {market_id[:16]}..."]
        if detail:
            parts.append(f"— {detail}")
        super().__init__(" ".join(parts))

    @property
    def is_recoverable(self) -> bool:
        # Recuperable: el mercado puede aparecer en el próximo discovery
        return True


class MarketExpiredError(MarketError):
    """
    Se intentó operar sobre un mercado cuyo end_date ya pasó.

    Se lanza antes de ejecutar cualquier orden para evitar operar
    en mercados expirados (lo que resultaría en error del CLOB).

    Ejemplo de uso:
        if market.expiry < datetime.now():
            raise MarketExpiredError(
                market_id=market.id,
                expiry=market.expiry,
            )
    """

    def __init__(
        self,
        market_id: str = "",
        expiry: "datetime | None" = None,
    ) -> None:
        self.market_id = market_id
        self.expiry = expiry

        msg = f"Mercado expirado: {market_id[:16]}..."
        if expiry:
            msg += f" (expiry={expiry.isoformat()})"
        super().__init__(msg)

    @property
    def is_recoverable(self) -> bool:
        # No recuperable: un mercado expirado no vuelve a abrirse
        return False


# ---------------------------------------------------------------------------
# Errores de ejecución (Execution — D-14, D-15, D-33)
# ---------------------------------------------------------------------------

class ExecutionError(DomainError):
    """Base para errores del ciclo de ejecución de órdenes."""


class OrderSubmitError(ExecutionError):
    """
    El CLOB de Polymarket rechazó o falló al procesar una orden.

    El real_handler la lanza tras agotar los 3 reintentos con backoff
    exponencial. En ese punto la Order ya tiene status=FAILED en DB
    y está registrada en audit_log.

    El operador recibe notificación en Telegram con el order_id local
    para poder hacer seguimiento manual.
    """

    def __init__(
        self,
        order_id: str = "",
        attempt: int = 0,
        clob_error: str = "",
    ) -> None:
        self.order_id = order_id
        self.attempt = attempt
        self.clob_error = clob_error

        super().__init__(
            f"Submit fallido para order_id={order_id} "
            f"tras {attempt} intentos: {clob_error}"
        )

    @property
    def is_recoverable(self) -> bool:
        return False


class OrderIdempotencyError(ExecutionError):
    """
    Ya existe una orden activa para este market_id.

    El real_handler verifica idempotencia antes de hacer submit al CLOB
    (Decisión D-33). Si encuentra una orden con status IN (PENDING,
    CONFIRMED, SUBMITTED) para el mismo market_id, lanza esta excepción
    en lugar de duplicar la orden.

    Nota: La orden duplicada candidata se cancela automáticamente
    (status=CANCELLED) antes de lanzar esta excepción.
    """

    def __init__(
        self,
        market_id: str = "",
        existing_order_id: str = "",
    ) -> None:
        self.market_id = market_id
        self.existing_order_id = existing_order_id

        super().__init__(
            f"Ya existe orden activa para market_id={market_id[:16]}... "
            f"(existing_order_id={existing_order_id})"
        )

    @property
    def is_recoverable(self) -> bool:
        # Recuperable: la próxima señal intentará en el siguiente ciclo
        return True


class ConfirmationTimeoutError(ExecutionError):
    """
    El operador no confirmó la orden en el tiempo límite (60 segundos).

    La orden se cancela automáticamente (status=CANCELLED) cuando se
    lanza esta excepción. No es un error — es el comportamiento esperado
    cuando el operador no responde. El bot sigue operando normalmente.

    Decisión D-15: timeout de confirmación = 60 segundos inamovible.
    """

    def __init__(
        self,
        order_id: str = "",
        timeout_seconds: int = 60,
    ) -> None:
        self.order_id = order_id
        self.timeout_seconds = timeout_seconds

        super().__init__(
            f"Confirmación no recibida para order_id={order_id} "
            f"en {timeout_seconds}s — orden cancelada automáticamente"
        )

    @property
    def is_recoverable(self) -> bool:
        return True


class CircuitBreakerOpenError(ExecutionError):
    """
    El circuit breaker está abierto — todas las llamadas al CLOB
    están bloqueadas temporalmente para prevenir cascada de fallos.

    Se lanza cuando el circuit breaker ha registrado 5 fallos en
    una ventana de 60 segundos. Las órdenes se rechazan automáticamente
    hasta que el breaker pase a half-open (tras 60s de recovery).

    El RealTradingHandler captura esta excepción y devuelve
    TradeResult.success=False sin intentar contactar al CLOB.
    """

    def __init__(
        self,
        failure_count: int = 0,
        recovery_seconds: int = 60,
    ) -> None:
        self.failure_count = failure_count
        self.recovery_seconds = recovery_seconds

        super().__init__(
            f"Circuit breaker ABIERTO tras {failure_count} fallos. "
            f"Reintento en {recovery_seconds}s"
        )

    @property
    def is_recoverable(self) -> bool:
        # Recuperable: el breaker se cierra automáticamente tras recovery
        return True


# ---------------------------------------------------------------------------
# Errores de trading (Trading Engine)
# ---------------------------------------------------------------------------

class TradingError(DomainError):
    """Base para errores del ciclo de trading."""


class InsufficientBalanceError(TradingError):
    """
    Balance insuficiente para ejecutar la operación solicitada.

    Se lanza cuando el Risk Engine o el Execution Handler detectan
    que el balance disponible es menor que el amount requerido.

    Ejemplo de uso:
        if balance < amount:
            raise InsufficientBalanceError(
                available=balance,
                required=amount,
            )
    """

    def __init__(
        self,
        available: float = 0.0,
        required: float = 0.0,
        detail: str = "",
    ) -> None:
        self.available = available
        self.required = required
        self.detail = detail

        msg = (
            f"Balance insuficiente: disponible={available:.2f} USDC, "
            f"requerido={required:.2f} USDC"
        )
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)

    @property
    def is_recoverable(self) -> bool:
        # Recuperable: el balance puede aumentar en ciclos futuros
        return True


class PositionNotFoundError(TradingError):
    """
    No se encontró una posición abierta para el market_id dado.

    Se lanza cuando se intenta ejecutar una salida (execute_exit)
    pero no hay posición abierta para ese mercado.

    Ejemplo de uso:
        position = await self._repo.get_open_position(market_id)
        if position is None:
            raise PositionNotFoundError(market_id=market_id)
    """

    def __init__(self, market_id: str = "", detail: str = "") -> None:
        self.market_id = market_id
        self.detail = detail

        msg = f"Posición no encontrada para market_id={market_id[:16]}..."
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)

    @property
    def is_recoverable(self) -> bool:
        return True


class OrderRejectedError(TradingError):
    """
    La orden fue rechazada por el CLOB o por validación local.

    Se lanza cuando el CLOB responde con status REJECTED, o cuando
    la validación local (precios, cantidades) rechaza la orden
    antes de enviarla.

    Ejemplo de uso:
        if response.status == "REJECTED":
            raise OrderRejectedError(
                order_id=order_id,
                reason=response.get("error", "desconocido"),
            )
    """

    def __init__(
        self,
        order_id: str = "",
        reason: str = "",
    ) -> None:
        self.order_id = order_id
        self.reason = reason

        super().__init__(
            f"Orden rechazada: order_id={order_id} — {reason}"
        )

    @property
    def is_recoverable(self) -> bool:
        return False


class SignalRejectedError(TradingError):
    """
    La señal de la estrategia fue rechazada por los filtros.

    Se lanza cuando los filtros de estrategia (liquidez, spread,
    time, tick confirmation) rechazan una señal antes de que
    llegue al Risk Engine. No es un error — es comportamiento normal.

    Ejemplo de uso:
        if not tick.is_liquid_enough:
            raise SignalRejectedError(
                reason="liquidez insuficiente",
                filter_name="liquidity_filter",
            )
    """

    def __init__(
        self,
        reason: str = "",
        filter_name: str = "",
    ) -> None:
        self.reason = reason
        self.filter_name = filter_name

        super().__init__(
            f"Señal rechazada por filtro '{filter_name}': {reason}"
        )

    @property
    def is_recoverable(self) -> bool:
        # Recuperable: la señal puede pasar en el próximo tick
        return True


# ---------------------------------------------------------------------------
# Errores de riesgo (Risk Engine — D-13)
# ---------------------------------------------------------------------------

class RiskError(DomainError):
    """Base para errores del Risk Engine."""


class RiskDeniedError(RiskError):
    """
    El Risk Engine denegó la señal de la estrategia.

    No es un error de programación — es el comportamiento esperado
    cuando una señal viola una regla de riesgo activa. El StrategyEngine
    la captura en silencio y loggea como evento normal (no como excepción).

    Incluye el nombre de la regla que denegó y la razón textual para
    mostrar en Telegram si el operador tiene alertas de riesgo activas.

    Ejemplo de uso en RiskEngine:
        decision = await self.evaluate(signal)
        if not decision.allowed:
            raise RiskDeniedError(
                rule_name=decision.rule_name,
                reason=decision.reason,
                signal=signal,
            )
    """

    def __init__(
        self,
        rule_name: str = "",
        reason: str = "",
        signal: object = None,
    ) -> None:
        self.rule_name = rule_name
        self.reason = reason
        self.signal = signal

        super().__init__(
            f"Señal denegada por regla '{rule_name}': {reason}"
        )

    @property
    def is_recoverable(self) -> bool:
        # Recuperable: la próxima señal pasará si el contexto de riesgo cambia
        return True


# ---------------------------------------------------------------------------
# Errores de WebSocket (Decisión D-32)
# ---------------------------------------------------------------------------

class WebSocketError(DomainError):
    """Base para errores del cliente WebSocket de Polymarket."""


class WebSocketConnectionError(WebSocketError):
    """
    Fallo al conectar o mantener la conexión WebSocket al order book.

    El ws_client la lanza en cada intento fallido antes de aplicar
    el backoff exponencial (2^n segundos, máximo 5 reintentos).
    Cada instancia incluye el número de intento para que el caller
    pueda decidir si continuar reintentando.
    """

    def __init__(
        self,
        market_id: str = "",
        attempt: int = 0,
        max_attempts: int = 5,
        original_error: str = "",
    ) -> None:
        self.market_id = market_id
        self.attempt = attempt
        self.max_attempts = max_attempts
        self.original_error = original_error

        super().__init__(
            f"WS connection failed para market_id={market_id[:16]}... "
            f"(intento {attempt}/{max_attempts}): {original_error}"
        )

    @property
    def is_recoverable(self) -> bool:
        return self.attempt < self.max_attempts


class WebSocketMaxRetriesError(WebSocketError):
    """
    Se agotaron los 5 reintentos de reconexión WebSocket.

    Esta excepción escala al nivel superior del sistema — el MarketTimer
    la captura, notifica al operador vía Telegram, y detiene el ciclo
    de ese mercado hasta que el operador reinicie manualmente.

    Decisión D-32: máximo 5 reintentos, backoff 2^n segundos.
    """

    def __init__(
        self,
        market_id: str = "",
        max_attempts: int = 5,
    ) -> None:
        self.market_id = market_id
        self.max_attempts = max_attempts

        super().__init__(
            f"WebSocket sin conexión tras {max_attempts} reintentos "
            f"para market_id={market_id[:16]}... — intervención manual requerida"
        )

    @property
    def is_recoverable(self) -> bool:
        # No recuperable automáticamente — requiere reinicio manual
        return False


# ---------------------------------------------------------------------------
# Errores de configuración
# ---------------------------------------------------------------------------

class ConfigurationError(DomainError):
    """
    Variable de entorno requerida ausente o con formato inválido.

    Se lanza durante el bootstrap del sistema. Si se lanza, el bot
    no arranca — es intencional para evitar operar con config incompleta.

    Ejemplo de uso en check_env.py:
        if not os.getenv("POLYMARKET_PRIVATE_KEY"):
            raise ConfigurationError(
                variable="POLYMARKET_PRIVATE_KEY",
                reason="requerida para modo real trading",
            )
    """

    def __init__(
        self,
        variable: str = "",
        reason: str = "",
    ) -> None:
        self.variable = variable
        self.reason = reason

        super().__init__(
            f"Variable de entorno '{variable}' faltante o inválida"
            + (f": {reason}" if reason else "")
        )

    @property
    def is_recoverable(self) -> bool:
        return False


class InvalidConfigError(ConfigurationError):
    """
    Una variable de configuración tiene un valor fuera del rango permitido.

    Se lanza durante la validación de configuración en bootstrap.
    A diferencia de ConfigurationError (variable ausente), este error
    indica que la variable existe pero su valor es inválido.

    Ejemplo de uso:
        if threshold < 0.01 or threshold > 0.99:
            raise InvalidConfigError(
                variable="BAT_THRESHOLD",
                value=threshold,
                constraint="debe estar entre 0.01 y 0.99",
            )
    """

    def __init__(
        self,
        variable: str = "",
        value: object = None,
        constraint: str = "",
    ) -> None:
        self.variable = variable
        self.value = value
        self.constraint = constraint

        msg = f"Configuración inválida: '{variable}'={value!r}"
        if constraint:
            msg += f" — {constraint}"
        # Usamos DomainError.__init__ directamente para tener nuestro
        # propio mensaje, saltando el formato de ConfigurationError
        DomainError.__init__(self, msg)

    @property
    def is_recoverable(self) -> bool:
        return False


class MissingEnvironmentVariableError(ConfigurationError):
    """
    Una variable de entorno requerida no está definida.

    Caso específico de ConfigurationError para variables de entorno.
    Se lanza durante el bootstrap para dar un mensaje más descriptivo.

    Ejemplo de uso:
        if not os.getenv("DATABASE_URL"):
            raise MissingEnvironmentVariableError(
                variable="DATABASE_URL",
                hint="Configura la URL de conexión a PostgreSQL",
            )
    """

    def __init__(
        self,
        variable: str = "",
        hint: str = "",
    ) -> None:
        self.variable = variable
        self.hint = hint

        msg = f"Variable de entorno requerida no definida: {variable}"
        if hint:
            msg += f" — {hint}"
        # Usamos DomainError.__init__ directamente para tener nuestro
        # propio mensaje, saltando el formato de ConfigurationError
        DomainError.__init__(self, msg)

    @property
    def is_recoverable(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Errores de seguridad
# ---------------------------------------------------------------------------

class SecurityError(DomainError):
    """Base para errores relacionados con seguridad y autenticación."""


class AuthenticationError(SecurityError):
    """
    Fallo de autenticación: credenciales inválidas, token expirado,
    o firma incorrecta.

    Se lanza cuando el CLOB rechaza una request por autenticación,
    o cuando el SecurityGuard detecta credenciales inválidas.

    Ejemplo de uso:
        if response.status_code == 401:
            raise AuthenticationError(
                reason="API key inválida o expirada",
            )
    """

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(
            f"Error de autenticación: {reason}"
            if reason
            else "Error de autenticación"
        )

    @property
    def is_recoverable(self) -> bool:
        # No recuperable automáticamente — requiere nuevas credenciales
        return False


class RateLimitExceededError(SecurityError):
    """
    Se ha superado el límite de requests a la API de Polymarket.

    El rate limiter integrado en SecurityGuard bloquea requests
    adicionales cuando se excede el límite configurado.

    Ejemplo de uso:
        if not await self._rate_limiter.acquire():
            raise RateLimitExceededError(
                window_seconds=60,
                max_requests=100,
            )
    """

    def __init__(
        self,
        window_seconds: int = 60,
        max_requests: int = 100,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests

        super().__init__(
            f"Rate limit excedido: {max_requests} requests "
            f"en {window_seconds}s"
        )

    @property
    def is_recoverable(self) -> bool:
        # Recuperable: la ventana se resetea automáticamente
        return True


# ---------------------------------------------------------------------------
# Errores de infraestructura
# ---------------------------------------------------------------------------

class InfrastructureError(DomainError):
    """Base para errores de conexión a servicios externos (DB, Redis)."""


class DatabaseConnectionError(InfrastructureError):
    """
    No se pudo conectar a PostgreSQL o la conexión se perdió.

    Se lanza cuando el pool de conexiones está exhausto o la DB
    no responde. El sistema debe reintentar con backoff.

    Ejemplo de uso:
        try:
            await session.execute(select(1))
        except OperationalError as e:
            raise DatabaseConnectionError(original_error=str(e))
    """

    def __init__(self, original_error: str = "") -> None:
        self.original_error = original_error

        msg = "Error de conexión a la base de datos"
        if original_error:
            msg += f": {original_error}"
        super().__init__(msg)

    @property
    def is_recoverable(self) -> bool:
        # Recuperable con reintento
        return True


class RedisConnectionError(InfrastructureError):
    """
    No se pudo conectar a Redis o la conexión se perdió.

    Se lanza cuando Redis no está disponible. El sistema debe
    operar en modo degradado (sin caché) hasta que Redis vuelva.

    Ejemplo de uso:
        try:
            await redis.ping()
        except ConnectionError as e:
            raise RedisConnectionError(original_error=str(e))
    """

    def __init__(self, original_error: str = "") -> None:
        self.original_error = original_error

        msg = "Error de conexión a Redis"
        if original_error:
            msg += f": {original_error}"
        super().__init__(msg)

    @property
    def is_recoverable(self) -> bool:
        # Recuperable con reintento
        return True
