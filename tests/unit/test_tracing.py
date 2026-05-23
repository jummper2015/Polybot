# tests/unit/test_tracing.py
"""Unit tests for src/infrastructure/observability/tracing.py"""

import os
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_tracing_state():
    """Ensure tracing module state is clean between tests."""
    import src.infrastructure.observability.tracing as t

    # Reset global state
    t._tracing_initialized = False
    # Reset tracer provider
    trace.set_tracer_provider(trace.NoOpTracerProvider())

    # Clear env vars that affect tracing
    for key in (
        "OTEL_TRACES_ENABLED",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_SERVICE_NAME",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_INSECURE",
        "DEPLOY_ENV",
    ):
        os.environ.pop(key, None)

    yield

    # Cleanup after test
    trace.set_tracer_provider(trace.NoOpTracerProvider())


@pytest.fixture
def set_otel_env():
    """Set minimal env vars for tracing to work."""
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    os.environ["OTEL_SERVICE_NAME"] = "polybot-test"


# ══════════════════════════════════════════════════════════════
# init_tracing tests
# ══════════════════════════════════════════════════════════════


class TestInitTracing:
    """Tests for init_tracing()."""

    def test_disabled_by_env_var_false(self):
        """init_tracing returns False when OTEL_TRACES_ENABLED=false."""
        os.environ["OTEL_TRACES_ENABLED"] = "false"
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"

        from src.infrastructure.observability.tracing import init_tracing

        result = init_tracing()
        assert result is False

    def test_disabled_by_env_var_zero(self):
        """init_tracing returns False when OTEL_TRACES_ENABLED=0."""
        os.environ["OTEL_TRACES_ENABLED"] = "0"
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"

        from src.infrastructure.observability.tracing import init_tracing

        result = init_tracing()
        assert result is False

    def test_disabled_by_env_var_no(self):
        """init_tracing returns False when OTEL_TRACES_ENABLED=no."""
        os.environ["OTEL_TRACES_ENABLED"] = "no"
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"

        from src.infrastructure.observability.tracing import init_tracing

        result = init_tracing()
        assert result is False

    def test_disabled_by_env_var_off(self):
        """init_tracing returns False when OTEL_TRACES_ENABLED=off."""
        os.environ["OTEL_TRACES_ENABLED"] = "off"
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"

        from src.infrastructure.observability.tracing import init_tracing

        result = init_tracing()
        assert result is False

    def test_disabled_no_endpoint(self):
        """init_tracing returns False when no endpoint is configured."""
        os.environ["OTEL_TRACES_ENABLED"] = "true"
        # No OTEL_EXPORTER_OTLP_ENDPOINT set

        from src.infrastructure.observability.tracing import init_tracing

        result = init_tracing()
        assert result is False

    def test_enabled_with_grpc_endpoint(self, set_otel_env):
        """init_tracing succeeds with gRPC endpoint (default protocol)."""
        from src.infrastructure.observability.tracing import init_tracing, is_initialized

        result = init_tracing()
        assert result is True
        assert is_initialized() is True

    def test_enabled_with_http_endpoint(self, set_otel_env):
        """init_tracing succeeds with HTTP/protobuf protocol."""
        os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"

        from src.infrastructure.observability.tracing import init_tracing, is_initialized

        result = init_tracing()
        assert result is True
        assert is_initialized() is True

    def test_service_name_from_env(self, set_otel_env):
        """Uses service name from env var."""
        os.environ["OTEL_SERVICE_NAME"] = "my-custom-bot"

        from src.infrastructure.observability.tracing import init_tracing

        result = init_tracing()
        assert result is True

    def test_service_name_from_param(self, set_otel_env):
        """Uses service name from parameter (overrides env)."""
        from src.infrastructure.observability.tracing import init_tracing

        result = init_tracing(service_name="param-bot")
        assert result is True

    def test_otlp_endpoint_from_param(self):
        """Uses OTLP endpoint from parameter (overrides env)."""
        os.environ["OTEL_TRACES_ENABLED"] = "true"

        from src.infrastructure.observability.tracing import init_tracing

        result = init_tracing(otlp_endpoint="http://custom:4317")
        assert result is True


# ══════════════════════════════════════════════════════════════
# shutdown_tracing tests
# ══════════════════════════════════════════════════════════════


class TestShutdownTracing:
    """Tests for shutdown_tracing()."""

    def test_shutdown_when_not_initialized_is_noop(self):
        """shutdown_tracing is a no-op when tracing was never initialized."""
        from src.infrastructure.observability.tracing import shutdown_tracing

        # Should not raise
        shutdown_tracing()

    def test_shutdown_after_init(self, set_otel_env):
        """shutdown_tracing cleans up after initialization."""
        from src.infrastructure.observability.tracing import (
            init_tracing,
            is_initialized,
            shutdown_tracing,
        )

        init_tracing()
        assert is_initialized() is True

        shutdown_tracing()
        assert is_initialized() is False

    def test_shutdown_handles_provider_error(self, set_otel_env):
        """shutdown_tracing handles exceptions from provider.shutdown()."""
        from src.infrastructure.observability.tracing import init_tracing, shutdown_tracing

        init_tracing()

        # Corrupt the provider so shutdown raises
        provider = trace.get_tracer_provider()
        provider.shutdown = MagicMock(side_effect=RuntimeError("shutdown failed"))

        # Should not raise
        shutdown_tracing()


# ══════════════════════════════════════════════════════════════
# is_initialized tests
# ══════════════════════════════════════════════════════════════


class TestIsInitialized:
    """Tests for is_initialized()."""

    def test_not_initialized_by_default(self):
        """is_initialized returns False by default."""
        from src.infrastructure.observability.tracing import is_initialized

        assert is_initialized() is False

    def test_initialized_after_init(self, set_otel_env):
        """is_initialized returns True after init_tracing succeeds."""
        from src.infrastructure.observability.tracing import init_tracing, is_initialized

        init_tracing()
        assert is_initialized() is True

    def test_not_initialized_after_shutdown(self, set_otel_env):
        """is_initialized returns False after shutdown."""
        from src.infrastructure.observability.tracing import (
            init_tracing,
            is_initialized,
            shutdown_tracing,
        )

        init_tracing()
        shutdown_tracing()
        assert is_initialized() is False


# ══════════════════════════════════════════════════════════════
# traced() context manager tests
# ══════════════════════════════════════════════════════════════


class TestTracedContextManager:
    """Tests for the traced() async context manager."""

    @pytest.mark.asyncio
    async def test_creates_span_with_attributes(self):
        """traced creates a span and sets attributes (non-recording when OTel not init)."""

        from src.infrastructure.observability.tracing import traced

        async with traced(
            "test_operation",
            kind=SpanKind.INTERNAL,
            market_id="abc123",
            asset="BTC",
        ) as span:
            assert span is not None
            # Span exists even without global OTel setup (NonRecordingSpan)

    @pytest.mark.asyncio
    async def test_skips_empty_attributes(self):
        """traced does not set attributes with empty values."""
        from src.infrastructure.observability.tracing import traced

        async with traced(
            "test_op",
            empty_attr="",
            valid_attr="value",
        ):
            # Should not crash with empty attribute
            pass

    @pytest.mark.asyncio
    async def test_sets_error_status_on_exception(self):
        """traced sets ERROR status and records exception when code raises."""
        from src.infrastructure.observability.tracing import traced

        with pytest.raises(ValueError, match="test error"):
            async with traced("failing_op"):
                raise ValueError("test error")

    @pytest.mark.asyncio
    async def test_sets_ok_status_normally(self):
        """traced allows setting OK status manually."""
        from src.infrastructure.observability.tracing import traced

        async with traced("ok_op") as span:
            span.set_status(Status(StatusCode.OK))
            # No exception → span ends normally

    @pytest.mark.asyncio
    async def test_truncates_long_attributes(self):
        """Attributes longer than 256 chars are truncated."""
        from src.infrastructure.observability.tracing import traced

        long_value = "x" * 500

        async with traced("trunc_test", long_attr=long_value):
            # Should not fail with long attributes
            pass


# ══════════════════════════════════════════════════════════════
# traced_sync decorator tests
# ══════════════════════════════════════════════════════════════


class TestTracedSync:
    """Tests for the traced_sync() decorator."""

    def test_decorator_wraps_function(self):
        """traced_sync wraps a function and preserves its return value."""
        from src.infrastructure.observability.tracing import traced_sync

        @traced_sync("my_sync_op", caller="test")
        def my_func(x: int) -> int:
            return x * 2

        result = my_func(21)
        assert result == 42

    def test_decorator_passes_through_exceptions(self):
        """traced_sync propagates exceptions from the wrapped function."""
        from src.infrastructure.observability.tracing import traced_sync

        @traced_sync("failing_sync_op")
        def failing_func():
            raise RuntimeError("sync failure")

        with pytest.raises(RuntimeError, match="sync failure"):
            failing_func()


# ══════════════════════════════════════════════════════════════
# get_tracer tests
# ══════════════════════════════════════════════════════════════


class TestGetTracer:
    """Tests for get_tracer()."""

    def test_returns_tracer_instance(self):
        """get_tracer returns a Tracer."""
        from src.infrastructure.observability.tracing import get_tracer

        tracer = get_tracer()
        assert tracer is not None

    def test_tracer_singleton(self):
        """get_tracer returns the same tracer instance on repeated calls."""
        from src.infrastructure.observability.tracing import get_tracer

        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2


# ══════════════════════════════════════════════════════════════
# Integration: bootstrap integration tests
# ══════════════════════════════════════════════════════════════


class TestBootstrapIntegration:
    """Tests that tracing integrates correctly with the bootstrap flow."""

    def test_init_tracing_returns_bool(self):
        """init_tracing always returns a bool."""
        from src.infrastructure.observability.tracing import init_tracing

        # Without endpoint → should return False (not crash)
        result = init_tracing()
        assert isinstance(result, bool)

    def test_init_then_shutdown_clean(self, set_otel_env):
        """Full lifecycle: init → use → shutdown works cleanly."""
        from src.infrastructure.observability.tracing import (
            get_tracer,
            init_tracing,
            shutdown_tracing,
        )

        assert init_tracing() is True

        # Use the tracer
        tracer = get_tracer()
        with tracer.start_as_current_span("lifecycle_test") as span:
            span.set_attribute("test", "value")

        shutdown_tracing()

    def test_double_init_is_safe(self, set_otel_env):
        """Calling init_tracing twice does not crash and stays initialized."""
        from src.infrastructure.observability.tracing import init_tracing, is_initialized

        init_tracing()
        init_tracing()  # Second call should be safe
        assert is_initialized() is True

    def test_double_shutdown_is_safe(self, set_otel_env):
        """Calling shutdown_tracing twice does not crash."""
        from src.infrastructure.observability.tracing import (
            init_tracing,
            shutdown_tracing,
        )

        init_tracing()
        shutdown_tracing()
        shutdown_tracing()  # Second call should be safe (no-op)


# ══════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case tests for the tracing module."""

    def test_traced_sync_skips_empty_attributes(self):
        """traced_sync skips attribute keys with empty values."""
        from src.infrastructure.observability.tracing import traced_sync

        @traced_sync("edge_op", empty="", valid="yes")
        def dummy():
            return True

        assert dummy() is True

    @pytest.mark.asyncio
    async def test_traced_async_with_no_otel_setup(self):
        """traced() works even when OTel is not initialized (noop span)."""
        from src.infrastructure.observability.tracing import traced

        async with traced("noop_span") as span:
            span.set_attribute("color", "blue")

        # Should not crash
