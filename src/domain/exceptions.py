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
  PolyBotError
  ├── MarketError
  │   ├── NoActiveMarketsError
  │   └── MarketFilterError
  ├── ExecutionError
  │   ├── OrderSubmitError
  │   ├── OrderIdempotencyError
  │   └── ConfirmationTimeoutError
  ├── RiskError
  │   └── RiskDeniedError
  ├── WebSocketError
  │   ├── WebSocketConnectionError
  │   └── WebSocketMaxRetriesError
  └── ConfigurationError
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imports solo para type hints — evita dependencias circulares en runtime
    from src.domain.enums.asset import Asset
    from src.domain.enums.window import Window


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class PolyBotError(Exception):
    """
    Raíz de todas las excepciones de dominio del bot.

    Permite capturar cualquier error de negocio con un solo except:
        except PolyBotError as e:
            log.error("domain_error", error=str(e), type=type(e).__name__)
    """


# ---------------------------------------------------------------------------
# Errores de mercado (Market Discovery — D-01, D-02, D-23)
# ---------------------------------------------------------------------------

class MarketError(PolyBotError):
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


# ---------------------------------------------------------------------------
# Errores de ejecución (Execution — D-14, D-15, D-33)
# ---------------------------------------------------------------------------

class ExecutionError(PolyBotError):
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


# ---------------------------------------------------------------------------
# Errores de riesgo (Risk Engine — D-13)
# ---------------------------------------------------------------------------

class RiskError(PolyBotError):
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

class WebSocketError(PolyBotError):
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

class ConfigurationError(PolyBotError):
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