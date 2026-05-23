"""
tests/unit/test_domain_exceptions.py
=====================================
Tests unitarios para todas las excepciones de dominio del bot.

Ejecutar con:
    pytest tests/unit/test_domain_exceptions.py -v
    pytest tests/unit/test_domain_exceptions.py -v --tb=short

Cobertura esperada: 100% de src/domain/exceptions.py
"""

import os

# ---------------------------------------------------------------------------
# Importar todas las excepciones a probar
# ---------------------------------------------------------------------------
# En tu proyecto real, el import sería:
#   from src.domain.exceptions import (...)
# Aquí usamos la ruta relativa del archivo que acabamos de crear.
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from src.domain.exceptions import (
    AuthenticationError,
    CircuitBreakerOpenError,
    ConfigurationError,
    ConfirmationTimeoutError,
    DatabaseConnectionError,
    DomainError,
    ExecutionError,
    InfrastructureError,
    InsufficientBalanceError,
    InvalidConfigError,
    MarketError,
    MarketExpiredError,
    MarketFilterError,
    MarketNotFoundError,
    MissingEnvironmentVariableError,
    NoActiveMarketsError,
    OrderIdempotencyError,
    OrderRejectedError,
    OrderSubmitError,
    PolyBotError,
    PositionNotFoundError,
    RateLimitExceededError,
    RedisConnectionError,
    RiskDeniedError,
    RiskError,
    SecurityError,
    SignalRejectedError,
    TradingError,
    WebSocketConnectionError,
    WebSocketError,
    WebSocketMaxRetriesError,
)

# ===========================================================================
# Jerarquía de herencia
# ===========================================================================

class TestHierarchy:
    """Verifica que toda excepción herede correctamente de PolyBotError."""

    def test_no_active_markets_is_market_error(self):
        assert issubclass(NoActiveMarketsError, MarketError)

    def test_no_active_markets_is_polybot_error(self):
        assert issubclass(NoActiveMarketsError, PolyBotError)

    def test_market_filter_is_market_error(self):
        assert issubclass(MarketFilterError, MarketError)

    def test_order_submit_is_execution_error(self):
        assert issubclass(OrderSubmitError, ExecutionError)

    def test_order_idempotency_is_execution_error(self):
        assert issubclass(OrderIdempotencyError, ExecutionError)

    def test_confirmation_timeout_is_execution_error(self):
        assert issubclass(ConfirmationTimeoutError, ExecutionError)

    def test_risk_denied_is_risk_error(self):
        assert issubclass(RiskDeniedError, RiskError)

    def test_ws_connection_is_websocket_error(self):
        assert issubclass(WebSocketConnectionError, WebSocketError)

    def test_ws_max_retries_is_websocket_error(self):
        assert issubclass(WebSocketMaxRetriesError, WebSocketError)

    def test_configuration_is_polybot_error(self):
        assert issubclass(ConfigurationError, PolyBotError)

    def test_domain_error_alias_equals_polybot_error(self):
        """DomainError y PolyBotError son la misma clase."""
        assert DomainError is PolyBotError

    def test_market_not_found_is_market_error(self):
        assert issubclass(MarketNotFoundError, MarketError)

    def test_market_expired_is_market_error(self):
        assert issubclass(MarketExpiredError, MarketError)

    def test_insufficient_balance_is_trading_error(self):
        assert issubclass(InsufficientBalanceError, TradingError)

    def test_position_not_found_is_trading_error(self):
        assert issubclass(PositionNotFoundError, TradingError)

    def test_order_rejected_is_trading_error(self):
        assert issubclass(OrderRejectedError, TradingError)

    def test_signal_rejected_is_trading_error(self):
        assert issubclass(SignalRejectedError, TradingError)

    def test_circuit_breaker_is_execution_error(self):
        assert issubclass(CircuitBreakerOpenError, ExecutionError)

    def test_invalid_config_is_configuration_error(self):
        assert issubclass(InvalidConfigError, ConfigurationError)

    def test_missing_env_var_is_configuration_error(self):
        assert issubclass(MissingEnvironmentVariableError, ConfigurationError)

    def test_authentication_is_security_error(self):
        assert issubclass(AuthenticationError, SecurityError)

    def test_rate_limit_is_security_error(self):
        assert issubclass(RateLimitExceededError, SecurityError)

    def test_db_connection_is_infrastructure_error(self):
        assert issubclass(DatabaseConnectionError, InfrastructureError)

    def test_redis_connection_is_infrastructure_error(self):
        assert issubclass(RedisConnectionError, InfrastructureError)

    def test_trading_error_is_domain_error(self):
        assert issubclass(TradingError, DomainError)

    def test_security_error_is_domain_error(self):
        assert issubclass(SecurityError, DomainError)

    def test_infrastructure_error_is_domain_error(self):
        assert issubclass(InfrastructureError, DomainError)

    def test_catch_all_with_polybot_error(self):
        """Un solo except PolyBotError captura todas las excepciones."""
        exceptions_to_test = [
            NoActiveMarketsError(),
            MarketFilterError(),
            OrderSubmitError(),
            OrderIdempotencyError(),
            ConfirmationTimeoutError(),
            RiskDeniedError(),
            WebSocketConnectionError(),
            WebSocketMaxRetriesError(),
            ConfigurationError(),
            MarketNotFoundError(),
            MarketExpiredError(),
            InsufficientBalanceError(),
            PositionNotFoundError(),
            OrderRejectedError(),
            SignalRejectedError(),
            CircuitBreakerOpenError(),
            InvalidConfigError(),
            MissingEnvironmentVariableError(),
            AuthenticationError(),
            RateLimitExceededError(),
            DatabaseConnectionError(),
            RedisConnectionError(),
        ]
        for exc in exceptions_to_test:
            assert isinstance(exc, PolyBotError), (
                f"{type(exc).__name__} no hereda de PolyBotError"
            )

    def test_catch_all_with_domain_error(self):
        """DomainError también captura todas las excepciones nuevas."""
        exceptions_to_test = [
            MarketNotFoundError(),
            MarketExpiredError(),
            InsufficientBalanceError(),
            PositionNotFoundError(),
            OrderRejectedError(),
            SignalRejectedError(),
            CircuitBreakerOpenError(),
            InvalidConfigError(),
            MissingEnvironmentVariableError(),
            AuthenticationError(),
            RateLimitExceededError(),
            DatabaseConnectionError(),
            RedisConnectionError(),
        ]
        for exc in exceptions_to_test:
            assert isinstance(exc, DomainError), (
                f"{type(exc).__name__} no hereda de DomainError"
            )


# ===========================================================================
# NoActiveMarketsError
# ===========================================================================

class TestNoActiveMarketsError:

    def test_sin_argumentos(self):
        """Se puede instanciar sin argumentos para uso rápido."""
        exc = NoActiveMarketsError()
        assert "No hay mercados activos" in str(exc)
        assert exc.asset is None
        assert exc.window is None
        assert exc.total_fetched == 0

    def test_con_total_fetched(self):
        exc = NoActiveMarketsError(total_fetched=47)
        assert "47" in str(exc)
        assert exc.total_fetched == 47

    def test_con_detail(self):
        exc = NoActiveMarketsError(detail="API retornó lista vacía")
        assert "API retornó lista vacía" in str(exc)

    def test_es_recuperable(self):
        """El MarketTimer puede reintentar en el próximo ciclo."""
        exc = NoActiveMarketsError()
        assert exc.is_recoverable is True

    def test_es_excepcion_lanzable(self):
        """Se puede usar en un bloque raise/except real."""
        with pytest.raises(NoActiveMarketsError) as exc_info:
            raise NoActiveMarketsError(total_fetched=10, detail="prueba")
        assert exc_info.value.total_fetched == 10

    def test_capturada_como_market_error(self):
        """Se captura con except MarketError."""
        with pytest.raises(MarketError):
            raise NoActiveMarketsError()

    def test_capturada_como_polybot_error(self):
        """Se captura con except PolyBotError."""
        with pytest.raises(PolyBotError):
            raise NoActiveMarketsError()

    def test_mensaje_sin_activo_ni_ventana(self):
        exc = NoActiveMarketsError()
        msg = str(exc)
        assert "BTC" not in msg
        assert "ETH" not in msg
        assert "5m" not in msg


# ===========================================================================
# MarketFilterError
# ===========================================================================

class TestMarketFilterError:

    def test_sin_argumentos(self):
        exc = MarketFilterError()
        assert "Error al filtrar mercado" in str(exc)

    def test_con_market_id_parcial(self):
        """El market_id se trunca a 16 chars en el mensaje."""
        market_id = "abc123def456ghi789xyz"  # 21 chars
        exc = MarketFilterError(market_id=market_id)
        # Solo los primeros 16 chars aparecen en el mensaje
        assert market_id[:16] in str(exc)

    def test_con_reason(self):
        exc = MarketFilterError(reason="yes_token_id == no_token_id")
        assert "yes_token_id == no_token_id" in str(exc)

    def test_no_es_recuperable(self):
        """Requiere diagnóstico manual — el mercado tiene datos malformados."""
        exc = MarketFilterError()
        assert exc.is_recoverable is False

    def test_atributos_accesibles(self):
        exc = MarketFilterError(
            market_id="mkt-001",
            reason="tokens duplicados",
            raw_question="Will BTC/ETH go up?",
            detail="campo tokens[0] == tokens[1]",
        )
        assert exc.market_id == "mkt-001"
        assert exc.reason == "tokens duplicados"
        assert exc.raw_question == "Will BTC/ETH go up?"
        assert exc.detail == "campo tokens[0] == tokens[1]"

    def test_es_lanzable_y_capturada_como_market_error(self):
        with pytest.raises(MarketError):
            raise MarketFilterError(market_id="x", reason="test")

    def test_tokens_identicos_caso_real(self):
        """Simula el caso más común: yes_token_id == no_token_id."""
        yes_id = "0xabc123"
        exc = MarketFilterError(
            market_id="condition-abc",
            reason=f"yes_token_id y no_token_id son idénticos ({yes_id})",
        )
        assert "idénticos" in str(exc)
        assert exc.is_recoverable is False


# ===========================================================================
# OrderSubmitError
# ===========================================================================

class TestOrderSubmitError:

    def test_sin_argumentos(self):
        exc = OrderSubmitError()
        assert "Submit fallido" in str(exc)

    def test_con_todos_los_campos(self):
        exc = OrderSubmitError(
            order_id="uuid-123",
            attempt=3,
            clob_error="502 Bad Gateway",
        )
        assert "uuid-123" in str(exc)
        assert "3" in str(exc)
        assert "502 Bad Gateway" in str(exc)

    def test_no_es_recuperable(self):
        exc = OrderSubmitError(attempt=3)
        assert exc.is_recoverable is False


# ===========================================================================
# OrderIdempotencyError
# ===========================================================================

class TestOrderIdempotencyError:

    def test_mensaje_contiene_market_id_truncado(self):
        market_id = "market-btc-5m-1234567890"
        exc = OrderIdempotencyError(market_id=market_id, existing_order_id="ord-99")
        assert market_id[:16] in str(exc)

    def test_es_recuperable(self):
        """La próxima señal intentará en el siguiente ciclo."""
        exc = OrderIdempotencyError()
        assert exc.is_recoverable is True

    def test_atributos_accesibles(self):
        exc = OrderIdempotencyError(
            market_id="mkt-btc",
            existing_order_id="ord-abc",
        )
        assert exc.market_id == "mkt-btc"
        assert exc.existing_order_id == "ord-abc"


# ===========================================================================
# ConfirmationTimeoutError
# ===========================================================================

class TestConfirmationTimeoutError:

    def test_timeout_defecto_es_60_segundos(self):
        """Decisión D-15: timeout inamovible de 60 segundos."""
        exc = ConfirmationTimeoutError(order_id="ord-x")
        assert "60" in str(exc)
        assert exc.timeout_seconds == 60

    def test_es_recuperable(self):
        """No es un fallo — el bot sigue operando normalmente."""
        exc = ConfirmationTimeoutError()
        assert exc.is_recoverable is True

    def test_mensaje_menciona_cancelada(self):
        exc = ConfirmationTimeoutError(order_id="ord-123")
        assert "cancelada" in str(exc)


# ===========================================================================
# RiskDeniedError
# ===========================================================================

class TestRiskDeniedError:

    def test_sin_argumentos(self):
        exc = RiskDeniedError()
        assert "denegada" in str(exc)

    def test_con_regla_y_razon(self):
        exc = RiskDeniedError(
            rule_name="max_exposure",
            reason="capital expuesto supera el 20% del portfolio",
        )
        assert "max_exposure" in str(exc)
        assert "20%" in str(exc)

    def test_es_recuperable(self):
        """La próxima señal puede pasar si el contexto de riesgo cambia."""
        exc = RiskDeniedError(rule_name="drawdown")
        assert exc.is_recoverable is True

    def test_atributos_accesibles(self):
        exc = RiskDeniedError(
            rule_name="min_balance",
            reason="balance insuficiente",
        )
        assert exc.rule_name == "min_balance"
        assert exc.reason == "balance insuficiente"


# ===========================================================================
# WebSocketConnectionError
# ===========================================================================

class TestWebSocketConnectionError:

    def test_recuperable_mientras_hay_intentos(self):
        exc = WebSocketConnectionError(attempt=3, max_attempts=5)
        assert exc.is_recoverable is True

    def test_no_recuperable_cuando_se_agotan_intentos(self):
        exc = WebSocketConnectionError(attempt=5, max_attempts=5)
        assert exc.is_recoverable is False

    def test_mensaje_contiene_intento(self):
        exc = WebSocketConnectionError(
            market_id="mkt-btc-5m",
            attempt=2,
            max_attempts=5,
            original_error="Connection refused",
        )
        assert "2/5" in str(exc)
        assert "Connection refused" in str(exc)

    def test_market_id_truncado(self):
        market_id = "condition-btc-5m-1234567890abcdef"
        exc = WebSocketConnectionError(market_id=market_id)
        assert market_id[:16] in str(exc)


# ===========================================================================
# WebSocketMaxRetriesError
# ===========================================================================

class TestWebSocketMaxRetriesError:

    def test_no_es_recuperable(self):
        """Requiere reinicio manual — Decisión D-32."""
        exc = WebSocketMaxRetriesError(market_id="mkt-x", max_attempts=5)
        assert exc.is_recoverable is False

    def test_mensaje_menciona_intervencion_manual(self):
        exc = WebSocketMaxRetriesError(max_attempts=5)
        assert "manual" in str(exc)

    def test_atributos_accesibles(self):
        exc = WebSocketMaxRetriesError(market_id="mkt-btc", max_attempts=5)
        assert exc.market_id == "mkt-btc"
        assert exc.max_attempts == 5


# ===========================================================================
# ConfigurationError
# ===========================================================================

class TestConfigurationError:

    def test_sin_argumentos(self):
        exc = ConfigurationError()
        assert "faltante o inválida" in str(exc)

    def test_con_variable_y_razon(self):
        exc = ConfigurationError(
            variable="POLYMARKET_PRIVATE_KEY",
            reason="requerida para modo real trading",
        )
        assert "POLYMARKET_PRIVATE_KEY" in str(exc)
        assert "modo real trading" in str(exc)

    def test_no_es_recuperable(self):
        """El bot no debe arrancar con config incompleta."""
        exc = ConfigurationError(variable="TELEGRAM_BOT_TOKEN")
        assert exc.is_recoverable is False

    def test_atributos_accesibles(self):
        exc = ConfigurationError(
            variable="REDIS_URL",
            reason="formato inválido",
        )
        assert exc.variable == "REDIS_URL"
        assert exc.reason == "formato inválido"


# ===========================================================================
# MarketNotFoundError
# ===========================================================================

class TestMarketNotFoundError:

    def test_mensaje_contiene_market_id_truncado(self):
        market_id = "condition-btc-5m-extra-largo"
        exc = MarketNotFoundError(market_id=market_id)
        assert market_id[:16] in str(exc)

    def test_con_detail(self):
        exc = MarketNotFoundError(market_id="mkt-1", detail="no existe en DB ni Redis")
        assert "no existe en DB ni Redis" in str(exc)

    def test_es_recuperable(self):
        exc = MarketNotFoundError()
        assert exc.is_recoverable is True


# ===========================================================================
# MarketExpiredError
# ===========================================================================

class TestMarketExpiredError:

    def test_mensaje_contiene_market_id(self):
        exc = MarketExpiredError(market_id="mkt-expired-001")
        assert "mkt-expired-001" in str(exc)

    def test_con_expiry(self):
        from datetime import datetime
        expiry = datetime(2024, 1, 15, 12, 0, 0)
        exc = MarketExpiredError(market_id="mkt-1", expiry=expiry)
        assert "2024-01-15" in str(exc)

    def test_no_es_recuperable(self):
        exc = MarketExpiredError()
        assert exc.is_recoverable is False


# ===========================================================================
# InsufficientBalanceError
# ===========================================================================

class TestInsufficientBalanceError:

    def test_muestra_valores(self):
        exc = InsufficientBalanceError(available=5.0, required=10.0)
        assert "5.00" in str(exc)
        assert "10.00" in str(exc)

    def test_con_detail(self):
        exc = InsufficientBalanceError(
            available=3.0, required=10.0, detail="paper balance"
        )
        assert "paper balance" in str(exc)

    def test_es_recuperable(self):
        exc = InsufficientBalanceError()
        assert exc.is_recoverable is True

    def test_atributos(self):
        exc = InsufficientBalanceError(available=5.5, required=10.5)
        assert exc.available == 5.5
        assert exc.required == 10.5


# ===========================================================================
# PositionNotFoundError
# ===========================================================================

class TestPositionNotFoundError:

    def test_mensaje_contiene_market_id(self):
        exc = PositionNotFoundError(market_id="mkt-btc-5m")
        assert "mkt-btc-5m" in str(exc)

    def test_con_detail(self):
        exc = PositionNotFoundError(market_id="mkt-1", detail="ya fue cerrada")
        assert "ya fue cerrada" in str(exc)

    def test_es_recuperable(self):
        exc = PositionNotFoundError()
        assert exc.is_recoverable is True


# ===========================================================================
# OrderRejectedError
# ===========================================================================

class TestOrderRejectedError:

    def test_mensaje_contiene_order_id(self):
        exc = OrderRejectedError(order_id="ord-123", reason="precio fuera de rango")
        assert "ord-123" in str(exc)
        assert "precio fuera de rango" in str(exc)

    def test_no_es_recuperable(self):
        exc = OrderRejectedError()
        assert exc.is_recoverable is False


# ===========================================================================
# SignalRejectedError
# ===========================================================================

class TestSignalRejectedError:

    def test_mensaje_contiene_filtro(self):
        exc = SignalRejectedError(
            reason="liquidez insuficiente",
            filter_name="liquidity_filter",
        )
        assert "liquidity_filter" in str(exc)
        assert "liquidez insuficiente" in str(exc)

    def test_es_recuperable(self):
        exc = SignalRejectedError()
        assert exc.is_recoverable is True

    def test_atributos(self):
        exc = SignalRejectedError(
            reason="spread demasiado alto",
            filter_name="spread_filter",
        )
        assert exc.reason == "spread demasiado alto"
        assert exc.filter_name == "spread_filter"


# ===========================================================================
# CircuitBreakerOpenError
# ===========================================================================

class TestCircuitBreakerOpenError:

    def test_mensaje_contiene_fallos(self):
        exc = CircuitBreakerOpenError(failure_count=5, recovery_seconds=60)
        assert "5" in str(exc)
        assert "60" in str(exc)

    def test_es_recuperable(self):
        exc = CircuitBreakerOpenError()
        assert exc.is_recoverable is True

    def test_atributos(self):
        exc = CircuitBreakerOpenError(failure_count=7, recovery_seconds=30)
        assert exc.failure_count == 7
        assert exc.recovery_seconds == 30


# ===========================================================================
# InvalidConfigError
# ===========================================================================

class TestInvalidConfigError:

    def test_mensaje_contiene_variable_y_valor(self):
        exc = InvalidConfigError(
            variable="BAT_THRESHOLD",
            value=1.5,
            constraint="debe estar entre 0.01 y 0.99",
        )
        assert "BAT_THRESHOLD" in str(exc)
        assert "1.5" in str(exc)

    def test_no_es_recuperable(self):
        exc = InvalidConfigError()
        assert exc.is_recoverable is False

    def test_atributos(self):
        exc = InvalidConfigError(
            variable="POSITION_SIZE", value=-10, constraint="debe ser > 0"
        )
        assert exc.variable == "POSITION_SIZE"
        assert exc.value == -10
        assert exc.constraint == "debe ser > 0"


# ===========================================================================
# MissingEnvironmentVariableError
# ===========================================================================

class TestMissingEnvironmentVariableError:

    def test_mensaje_contiene_variable(self):
        exc = MissingEnvironmentVariableError(
            variable="DATABASE_URL",
            hint="Configura la URL de conexión",
        )
        assert "DATABASE_URL" in str(exc)
        assert "URL de conexión" in str(exc)

    def test_no_es_recuperable(self):
        exc = MissingEnvironmentVariableError(variable="REDIS_URL")
        assert exc.is_recoverable is False

    def test_atributos(self):
        exc = MissingEnvironmentVariableError(
            variable="API_KEY", hint="genera una en polymarket.com"
        )
        assert exc.variable == "API_KEY"
        assert exc.hint == "genera una en polymarket.com"


# ===========================================================================
# AuthenticationError
# ===========================================================================

class TestAuthenticationError:

    def test_mensaje_con_reason(self):
        exc = AuthenticationError(reason="API key expirada")
        assert "API key expirada" in str(exc)

    def test_mensaje_sin_reason(self):
        exc = AuthenticationError()
        assert "Error de autenticación" in str(exc)

    def test_no_es_recuperable(self):
        exc = AuthenticationError()
        assert exc.is_recoverable is False


# ===========================================================================
# RateLimitExceededError
# ===========================================================================

class TestRateLimitExceededError:

    def test_mensaje_contiene_limites(self):
        exc = RateLimitExceededError(window_seconds=60, max_requests=100)
        assert "100" in str(exc)
        assert "60" in str(exc)

    def test_es_recuperable(self):
        exc = RateLimitExceededError()
        assert exc.is_recoverable is True

    def test_atributos(self):
        exc = RateLimitExceededError(window_seconds=30, max_requests=50)
        assert exc.window_seconds == 30
        assert exc.max_requests == 50


# ===========================================================================
# DatabaseConnectionError
# ===========================================================================

class TestDatabaseConnectionError:

    def test_mensaje_con_error(self):
        exc = DatabaseConnectionError(original_error="Connection refused")
        assert "Connection refused" in str(exc)

    def test_mensaje_sin_error(self):
        exc = DatabaseConnectionError()
        assert "Error de conexión a la base de datos" in str(exc)

    def test_es_recuperable(self):
        exc = DatabaseConnectionError()
        assert exc.is_recoverable is True

    def test_atributos(self):
        exc = DatabaseConnectionError(original_error="timeout")
        assert exc.original_error == "timeout"


# ===========================================================================
# RedisConnectionError
# ===========================================================================

class TestRedisConnectionError:

    def test_mensaje_con_error(self):
        exc = RedisConnectionError(original_error="NOAUTH Authentication required")
        assert "NOAUTH" in str(exc)

    def test_mensaje_sin_error(self):
        exc = RedisConnectionError()
        assert "Error de conexión a Redis" in str(exc)

    def test_es_recuperable(self):
        exc = RedisConnectionError()
        assert exc.is_recoverable is True

    def test_atributos(self):
        exc = RedisConnectionError(original_error="connection timeout")
        assert exc.original_error == "connection timeout"


# ===========================================================================
# Casos de integración — uso real en el código del bot
# ===========================================================================

class TestIntegrationPatterns:
    """
    Verifica los patrones de uso exactos que aparecen en el código del bot.
    """

    def test_patron_market_service_sin_mercados(self):
        """
        Simula el Paso 2 del market_service cuando el filtro no encuentra nada.
        El market_service lanza, el MarketTimer captura y loggea.
        """
        raw_markets = [
            {"question": "Will SOL be higher?", "condition_id": "x"},  # activo incorrecto
            {"question": "Will BTC go up in 1 hour?", "condition_id": "y"},  # ventana incorrecta
        ]

        def filtrar(markets):
            validos = [
                m for m in markets
                if ("BTC" in m["question"] or "ETH" in m["question"])
                and ("5 minute" in m["question"] or "15 minute" in m["question"])
            ]
            if not validos:
                raise NoActiveMarketsError(total_fetched=len(markets))
            return validos

        with pytest.raises(NoActiveMarketsError) as exc_info:
            filtrar(raw_markets)

        assert exc_info.value.total_fetched == 2
        assert exc_info.value.is_recoverable is True

    def test_patron_tokens_identicos_lanza_filter_error(self):
        """
        Simula la validación del Paso 3 cuando yes_token_id == no_token_id.
        El infrastructure/polymarket/adapters.py lanza, el service descarta.
        """
        raw_market = {
            "condition_id": "bad-market-001",
            "tokens": [
                {"token_id": "0xSAME"},
                {"token_id": "0xSAME"},  # idéntico — mercado malformado
            ],
            "question": "Will BTC be up in 5 minutes?",
        }

        def build_market_info(raw):
            yes_id = raw["tokens"][0]["token_id"]
            no_id  = raw["tokens"][1]["token_id"]
            if yes_id == no_id:
                raise MarketFilterError(
                    market_id=raw["condition_id"],
                    reason="yes_token_id y no_token_id son idénticos",
                    raw_question=raw["question"],
                )

        with pytest.raises(MarketFilterError) as exc_info:
            build_market_info(raw_market)

        assert "bad-market-001" in exc_info.value.market_id
        assert exc_info.value.is_recoverable is False

    def test_patron_captura_general_en_market_timer(self):
        """
        El MarketTimer captura cualquier MarketError y decide si reintenta
        basándose en is_recoverable.
        """
        errores_y_recuperabilidad = [
            (NoActiveMarketsError(), True),
            (MarketFilterError(), False),
        ]

        for exc, esperado in errores_y_recuperabilidad:
            assert exc.is_recoverable == esperado, (
                f"{type(exc).__name__}.is_recoverable debería ser {esperado}"
            )

    def test_patron_captura_polybot_error_en_handler_telegram(self):
        """
        Los handlers de Telegram capturan PolyBotError y responden con
        un mensaje de error sin exponer detalles internos.
        """
        def handler_simulado():
            raise RiskDeniedError(
                rule_name="max_exposure",
                reason="20% superado",
            )

        try:
            handler_simulado()
        except PolyBotError as e:
            mensaje_para_usuario = f"⚠️ Operación no ejecutada: {type(e).__name__}"
            assert "RiskDeniedError" in mensaje_para_usuario
            # El mensaje NO incluye detalles internos del risk engine
            assert "max_exposure" not in mensaje_para_usuario
