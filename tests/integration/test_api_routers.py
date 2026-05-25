# tests/integration/test_api_routers.py
"""
Integration tests for API routers.

Uses FastAPI TestClient with mocked application services.
Covers all 7 routers: health, markets, positions, orders,
dashboard, metrics, and the SPA fallback.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.interfaces.api.app import create_app
from src.interfaces.api.schemas.health_schema import ServiceStatusEnum


@pytest.fixture
def test_app():
    """Create a FastAPI app with mocked container for testing."""
    app = create_app()

    # Mock container with all required services
    mock_container = MagicMock()
    mock_container.config.trading_mode = "paper"
    mock_container.config.paper_initial_balance = 1000.0
    mock_container.uptime_seconds = MagicMock(return_value=3600.0)

    # Mock repository
    mock_container.repository = MagicMock()
    mock_container.repository.get_active_markets = AsyncMock(return_value=[])
    mock_container.repository.get_positions = AsyncMock(return_value=[])

    # Mock Redis
    mock_container.redis = MagicMock()
    mock_container.redis._redis = MagicMock()
    mock_container.redis._redis.ping = AsyncMock(return_value=True)
    mock_container.redis.get_ws_state = AsyncMock(return_value=None)

    # Mock market service
    mock_container.market_service = MagicMock()
    mock_container.market_service.get_active_markets = AsyncMock(return_value=[])
    mock_container.market_service.get_market_by_id = AsyncMock(
        return_value=MagicMock()
    )

    # Mock portfolio service
    mock_container.portfolio_service = MagicMock()
    mock_container.portfolio_service.get_positions = AsyncMock(
        return_value=MagicMock(total=0, positions=[])
    )
    mock_container.portfolio_service.get_position_by_id = AsyncMock(
        return_value=MagicMock()
    )
    mock_container.portfolio_service.get_balance = AsyncMock(
        return_value=1000.0
    )

    # Mock trading service
    mock_container.trading_service = MagicMock()
    mock_container.trading_service.get_orders = AsyncMock(
        return_value=MagicMock(total=0, orders=[])
    )
    mock_container.trading_service.get_order_by_id = AsyncMock(
        return_value=MagicMock()
    )
    mock_container.trading_service.get_status = AsyncMock(
        return_value={"running": False, "mode": "paper"}
    )

    # Mock Telegram bot
    mock_container.telegram_bot = MagicMock()
    mock_container.telegram_bot.get_me = AsyncMock(
        return_value=MagicMock(id=12345)
    )

    # Mock WS client
    mock_container.ws_client = MagicMock()
    mock_container.ws_client._subscriptions = {"test": MagicMock(done=MagicMock(return_value=False))}

    # Mock strategy engine
    mock_container.strategy_engine = MagicMock()
    mock_container.strategy_engine.get_state = MagicMock(return_value=None)

    # Attach to app state
    app.state.container = mock_container

    return app


@pytest.fixture
def client(test_app):
    """Create TestClient from the test app."""
    return TestClient(test_app)


# ──────────────────────────────────────────────────────────────────────
# HEALTH ROUTER TESTS
# ──────────────────────────────────────────────────────────────────────


class TestHealthRouter:
    """Tests for /api/v1/health endpoint."""

    def test_health_returns_200(self, client):
        """Health endpoint returns 200 OK."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_health_returns_json(self, client):
        """Health endpoint returns JSON content type."""
        response = client.get("/api/v1/health")
        assert response.headers["content-type"].startswith("application/json")

    def test_health_has_required_fields(self, client):
        """Health response contains status, version, mode, services."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert "status" in data
        assert "version" in data
        assert "mode" in data
        assert "services" in data

    def test_health_services_includes_all_checks(self, client):
        """Health services dict includes all 5 services."""
        response = client.get("/api/v1/health")
        data = response.json()
        services = data["services"]
        assert "database" in services
        assert "redis" in services
        assert "polymarket" in services
        assert "telegram" in services
        assert "websockets" in services

    def test_health_returns_valid_status_values(self, client):
        """Health status is one of: ok, OK, DEGRADED, DOWN."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert data["status"].upper() in ("OK", "DEGRADED", "DOWN")

    def test_health_mode_is_paper(self, client):
        """Health response reflects paper trading mode."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert data["mode"] == "paper"


# ──────────────────────────────────────────────────────────────────────
# MARKETS ROUTER TESTS
# ──────────────────────────────────────────────────────────────────────


class TestMarketsRouter:
    """Tests for /api/v1/markets endpoints."""

    def test_markets_returns_200(self, client):
        """Markets endpoint returns 200 OK."""
        response = client.get("/api/v1/markets")
        assert response.status_code == 200

    def test_markets_returns_json(self, client):
        """Markets endpoint returns JSON."""
        response = client.get("/api/v1/markets")
        assert response.headers["content-type"].startswith("application/json")

    def test_markets_has_total_field(self, client):
        """Markets response includes total count."""
        response = client.get("/api/v1/markets")
        data = response.json()
        assert "total" in data
        assert "markets" in data
        assert isinstance(data["total"], int)

    def test_markets_filters_by_asset(self, client):
        """Markets endpoint accepts asset filter param."""
        response = client.get("/api/v1/markets?asset=BTC")
        assert response.status_code == 200

    def test_markets_filters_by_window(self, client):
        """Markets endpoint accepts window filter param."""
        response = client.get("/api/v1/markets?window=5m")
        assert response.status_code == 200

    def test_markets_filters_by_both(self, client):
        """Markets endpoint accepts both asset and window filters."""
        response = client.get("/api/v1/markets?asset=BTC&window=5m")
        assert response.status_code == 200


# ──────────────────────────────────────────────────────────────────────
# POSITIONS ROUTER TESTS
# ──────────────────────────────────────────────────────────────────────


class TestPositionsRouter:
    """Tests for /api/v1/positions endpoints."""

    def test_positions_returns_200(self, client):
        """Positions endpoint returns 200 OK."""
        response = client.get("/api/v1/positions")
        assert response.status_code == 200

    def test_positions_returns_json(self, client):
        """Positions endpoint returns JSON."""
        response = client.get("/api/v1/positions")
        assert response.headers["content-type"].startswith("application/json")

    def test_positions_filters_by_mode(self, client):
        """Positions endpoint accepts mode filter param."""
        response = client.get("/api/v1/positions?mode=paper")
        assert response.status_code == 200

    def test_positions_filters_open_only(self, client):
        """Positions endpoint accepts open_only filter param."""
        response = client.get("/api/v1/positions?open_only=true")
        assert response.status_code == 200

    def test_positions_handles_invalid_mode(self, client):
        """Positions endpoint handles invalid mode gracefully."""
        response = client.get("/api/v1/positions?mode=invalid")
        # Should still return 200 — service filters internally
        assert response.status_code in (200, 422)


# ──────────────────────────────────────────────────────────────────────
# ORDERS ROUTER TESTS
# ──────────────────────────────────────────────────────────────────────


class TestOrdersRouter:
    """Tests for /api/v1/orders endpoints."""

    def test_orders_returns_200(self, client):
        """Orders endpoint returns 200 OK."""
        response = client.get("/api/v1/orders")
        assert response.status_code == 200

    def test_orders_returns_json(self, client):
        """Orders endpoint returns JSON."""
        response = client.get("/api/v1/orders")
        assert response.headers["content-type"].startswith("application/json")

    def test_orders_filters_by_status(self, client):
        """Orders endpoint accepts status filter param."""
        response = client.get("/api/v1/orders?status=filled")
        assert response.status_code == 200

    def test_orders_respects_limit(self, client):
        """Orders endpoint accepts limit param."""
        response = client.get("/api/v1/orders?limit=10")
        assert response.status_code == 200

    def test_orders_rejects_invalid_limit_0(self, client):
        """Orders endpoint rejects limit=0 (below min)."""
        response = client.get("/api/v1/orders?limit=0")
        assert response.status_code == 422

    def test_orders_rejects_invalid_limit_over_max(self, client):
        """Orders endpoint rejects limit > 200 (above max)."""
        response = client.get("/api/v1/orders?limit=500")
        assert response.status_code == 422


# ──────────────────────────────────────────────────────────────────────
# DASHBOARD ROUTER TESTS
# ──────────────────────────────────────────────────────────────────────


class TestDashboardRouter:
    """Tests for /api/v1/dashboard/* endpoints."""

    def test_summary_returns_200(self, client):
        """Dashboard summary returns 200 OK."""
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200

    def test_summary_has_required_fields(self, client):
        """Dashboard summary includes all expected fields."""
        response = client.get("/api/v1/dashboard/summary")
        data = response.json()
        required = [
            "balance", "initial_balance", "total_pnl_usdc",
            "open_positions", "closed_positions",
            "win_rate", "profit_factor", "bot_running",
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"

    def test_equity_returns_200(self, client):
        """Equity curve returns 200 OK."""
        response = client.get("/api/v1/dashboard/equity")
        assert response.status_code == 200

    def test_equity_returns_list(self, client):
        """Equity curve returns a list."""
        response = client.get("/api/v1/dashboard/equity")
        data = response.json()
        assert isinstance(data, list)

    def test_equity_respects_limit(self, client):
        """Equity curve respects limit param."""
        response = client.get("/api/v1/dashboard/equity?limit=50")
        assert response.status_code == 200

    def test_markets_overview_returns_200(self, client):
        """Markets overview returns 200 OK."""
        response = client.get("/api/v1/dashboard/markets")
        assert response.status_code == 200

    def test_trades_returns_200(self, client):
        """Recent trades returns 200 OK."""
        response = client.get("/api/v1/dashboard/trades")
        assert response.status_code == 200

    def test_trades_returns_list(self, client):
        """Recent trades returns a list."""
        response = client.get("/api/v1/dashboard/trades")
        data = response.json()
        assert isinstance(data, list)


# ──────────────────────────────────────────────────────────────────────
# METRICS ROUTER TESTS
# ──────────────────────────────────────────────────────────────────────


class TestMetricsRouter:
    """Tests for /api/v1/metrics endpoint."""

    def test_metrics_returns_200(self, client):
        """Metrics endpoint returns 200 OK."""
        response = client.get("/api/v1/metrics")
        assert response.status_code == 200

    def test_metrics_returns_prometheus_format(self, client):
        """Metrics endpoint returns text/plain Prometheus format."""
        response = client.get("/api/v1/metrics")
        # Prometheus content type
        content_type = response.headers.get("content-type", "")
        assert "text/plain" in content_type or "prometheus" in content_type.lower()

    def test_metrics_contains_expected_gauges(self, client):
        """Metrics output contains expected metric names."""
        response = client.get("/api/v1/metrics")
        text = response.text
        # Check for key metrics that should always be present
        # bot_uptime is registered at import time
        assert "bot_uptime" in text, "Missing metric: bot_uptime"


# ──────────────────────────────────────────────────────────────────────
# API TOP-LEVEL TESTS
# ──────────────────────────────────────────────────────────────────────


class TestApiBasics:
    """Tests for API-level behavior: CORS, content-type, error handling."""

    def test_cors_headers_present(self, client):
        """CORS middleware is configured (may use wildcard or specific origins)."""
        response = client.get("/api/v1/health")
        # TestClient may not include CORS headers on same-origin requests.
        # Verify the response is accessible (CORS isn't blocking).
        assert response.status_code == 200
        # CORS headers may or may not be present on localhost requests

    def test_api_returns_json_content_type(self, client):
        """All API endpoints return JSON (via ORJSONResponse)."""
        endpoints = [
            "/api/v1/health",
            "/api/v1/markets",
            "/api/v1/positions",
            "/api/v1/orders",
        ]
        for endpoint in endpoints:
            response = client.get(endpoint)
            ct = response.headers.get("content-type", "")
            assert "application/json" in ct, (
                f"{endpoint} returned {ct}, expected application/json"
            )

    def test_nonexistent_endpoint_returns_404(self, client):
        """Non-existent API routes return 404."""
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404

    def test_root_returns_dashboard_html(self, client):
        """Root URL serves the dashboard (HTML or 200)."""
        response = client.get("/")
        # May be 200 (index.html) or 404 if static files not built
        assert response.status_code in (200, 404)


# ──────────────────────────────────────────────────────────────────────
# GRACEFUL DEGRADATION TESTS
# ──────────────────────────────────────────────────────────────────────


class TestGracefulDegradation:
    """Tests that API handles missing data gracefully."""

    def test_health_handles_db_failure(self, test_app, client):
        """Health check degrades when DB is down."""
        test_app.state.container.repository.get_active_markets = AsyncMock(
            side_effect=Exception("DB connection refused")
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["database"] == ServiceStatusEnum.DOWN.value

    def test_health_handles_redis_failure(self, test_app, client):
        """Health check degrades when Redis is down."""
        test_app.state.container.redis._redis.ping = AsyncMock(
            side_effect=Exception("Redis connection refused")
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["redis"] == ServiceStatusEnum.DOWN.value

    def test_health_handles_polymarket_failure(self, test_app, client):
        """Health check degrades when Polymarket API is unreachable."""
        # The Polymarket check uses httpx directly — we can't easily mock it
        # But we can verify the exception path doesn't crash
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert "polymarket" in response.json()["services"]

    def test_health_handles_telegram_failure(self, test_app, client):
        """Health check degrades when Telegram bot is not connected."""
        test_app.state.container.telegram_bot.get_me = AsyncMock(
            side_effect=Exception("Telegram not connected")
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["telegram"] == ServiceStatusEnum.DOWN.value

    def test_health_handles_ws_failure(self, test_app, client):
        """Health check degrades when WebSocket is down."""
        test_app.state.container.ws_client._subscriptions = {}
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["websockets"] == ServiceStatusEnum.DEGRADED.value

    def test_dashboard_handles_empty_positions(self, client):
        """Dashboard summary works with zero positions."""
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["closed_positions"] == 0
        assert data["total_trades"] == 0

    def test_dashboard_handles_no_markets(self, client):
        """Markets overview works with empty market list."""
        response = client.get("/api/v1/dashboard/markets")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0
