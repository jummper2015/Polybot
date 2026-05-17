"""
tests/unit/test_domain_exceptions.py
=====================================
Tests unitarios para todas las excepciones de dominio del bot.

Ejecutar con:
    pytest tests/unit/test_domain_exceptions.py -v
    pytest tests/unit/test_domain_exceptions.py -v --tb=short

Cobertura esperada: 100% de src/domain/exceptions.py
"""

import pytest

# ---------------------------------------------------------------------------
# Importar todas las excepciones a probar
# ---------------------------------------------------------------------------
# En tu proyecto real, el import sería:
#   from src.domain.exceptions import (...)
# Aquí usamos la ruta relativa del archivo que acabamos de crear.

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from src.domain.exceptions import (
    PolyBotError,
    MarketError,
    NoActiveMarketsError,
    MarketFilterError,
    ExecutionError,
    OrderSubmitError,
    OrderIdempotencyError,
    ConfirmationTimeoutError,
    RiskError,
    RiskDeniedError,
    WebSocketError,
    WebSocketConnectionError,
    WebSocketMaxRetriesError,
    ConfigurationError,
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
        ]
        for exc in exceptions_to_test:
            assert isinstance(exc, PolyBotError), (
                f"{type(exc).__name__} no hereda de PolyBotError"
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
        yes_id = no_id = "0xabc123"
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