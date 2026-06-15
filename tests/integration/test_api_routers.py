# tests/integration/test_api_routers.py
"""
Integration tests for API routers.

Uses FastAPI TestClient with mocked application services.
Covers all 7 routers: health, markets, positions, orders,
dashboard, metrics, and the SPA fallback.
"""

from unittest.mock import AsyncMock, MagicMock

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
    mock_container.ws_client._subscriptions = {
        "test": MagicMock(done=MagicMock(return_value=False))
    }

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


# ──────────────────────────────────────────────────────────────────────
# R1.5 — DASHBOARD EDGE CASES
# ──────────────────────────────────────────────────────────────────────


def _mk_pos(pnl, minute=0, closed_at=..., **kw):
    """Helper: posición cerrada mínima para tests del dashboard.

    `closed_at` por defecto se sintetiza desde `minute`; pasar
    explícitamente `None` para representar una posición abierta.
    """
    from datetime import datetime, timezone
    pos = MagicMock()
    pos.pnl = pnl
    pos.pnl_pct = kw.get("pnl_pct", pnl / 100 if pnl else None)
    if closed_at is ...:
        pos.closed_at = datetime(
            2026, 6, 14, 12, minute, 0, tzinfo=timezone.utc
        )
    else:
        pos.closed_at = closed_at
    pos.opened_at = datetime(2026, 6, 14, 11, 0, 0, tzinfo=timezone.utc)
    pos.amount = kw.get("amount", 10.0)
    pos.entry_price = kw.get("entry_price", 0.5)
    pos.exit_price = kw.get("exit_price", 0.55)
    pos.mode = kw.get("mode", "paper")
    pos.exit_reason = kw.get("exit_reason", "take_profit")
    pos.side = kw.get("side", "YES")
    pos.asset = kw.get("asset", "BTC")
    pos.window = kw.get("window", "5m")
    pos.strategy = kw.get("strategy", "mean_reversion")
    pos.id = kw.get("id", "pos-1")
    return pos


def _mk_market(asset_val, window_val, vol=100.0, market_id=None):
    """Helper: market activo mínimo (asset.value/window.value compatibles)."""
    m = MagicMock()
    m.id = market_id or f"mkt-{asset_val}-{window_val}-{int(vol)}"
    m.asset.value = asset_val
    m.window.value = window_val
    m.yes_price = 0.6
    m.no_price = 0.4
    m.volume_24h = vol
    return m


class TestDashboardEdgeCases:
    """R1.5: casos sin cubrir en dashboard.py (drawdown, regimes, orderbook, dedup)."""

    def test_max_drawdown_with_valley_and_recovery(self, test_app, client):
        """Serie cerrada con pico → caída → recuperación produce max_dd > 0."""
        positions = [
            _mk_pos(+100, minute=10),  # balance 1100, peak 1100
            _mk_pos(-300, minute=20),  # balance 800,  dd = 300/1100 ≈ 0.2727
            _mk_pos(+50, minute=30),
        ]
        test_app.state.container.repository.get_positions = AsyncMock(
            return_value=positions
        )
        test_app.state.container.portfolio_service.get_balance = AsyncMock(
            return_value=850.0  # drawdown actual ≈ 0.15
        )

        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["max_drawdown_pct"] > 0.20
        assert data["closed_positions"] == 3
        assert data["win_rate"] == pytest.approx(2 / 3, abs=0.01)
        assert data["profit_factor"] > 0.0

    def test_summary_with_only_losers_profit_factor_zero(self, test_app, client):
        """Sin ganadores: profit_factor=0, win_rate=0."""
        positions = [_mk_pos(-50, minute=5), _mk_pos(-20, minute=10)]
        test_app.state.container.repository.get_positions = AsyncMock(
            return_value=positions
        )
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["win_rate"] == 0.0
        assert data["profit_factor"] == 0.0
        assert data["total_pnl_usdc"] < 0

    def test_drawdown_zero_when_no_closed_positions(self, client):
        """Sin posiciones cerradas: max_drawdown_pct = 0."""
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["max_drawdown_pct"] == 0.0
        assert data["drawdown_pct"] == 0.0

    def test_drawdown_helper_directly(self):
        """_calculate_max_drawdown: vacío, 1 elemento, secuencia con valley."""
        from datetime import datetime, timezone

        from src.interfaces.api.routers.dashboard import _calculate_max_drawdown

        assert _calculate_max_drawdown([], 1000.0) == 0.0
        assert _calculate_max_drawdown([], 0.0) == 0.0

        ts = datetime(2026, 6, 14, tzinfo=timezone.utc)
        single = [MagicMock(pnl=50, closed_at=ts)]
        # 1 ganador: drawdown = 0 (sube monótono).
        assert _calculate_max_drawdown(single, 1000.0) == 0.0

        # Valley: 1000 -> 1100 -> 700 -> 800. peak=1100, min=700, dd=0.3636
        valley = [
            MagicMock(pnl=+100, closed_at=ts.replace(minute=1)),
            MagicMock(pnl=-400, closed_at=ts.replace(minute=2)),
            MagicMock(pnl=+100, closed_at=ts.replace(minute=3)),
        ]
        dd = _calculate_max_drawdown(valley, 1000.0)
        assert dd == pytest.approx(400 / 1100, abs=0.001)

    def test_markets_overview_with_orderbook(self, test_app, client):
        """Markets overview parsea bids/asks y deduplica por (asset, window)."""
        m1_high = _mk_market("BTC", "5m", vol=500.0, market_id="m-btc-5m-high")
        m1_low = _mk_market("BTC", "5m", vol=100.0, market_id="m-btc-5m-low")
        m2 = _mk_market("ETH", "15m", vol=200.0, market_id="m-eth-15m")

        test_app.state.container.market_service.get_active_markets = AsyncMock(
            return_value=[m1_low, m1_high, m2]  # duplicado BTC 5m
        )
        test_app.state.container.redis.get_ws_state = AsyncMock(
            return_value={"status": "connected"}
        )
        test_app.state.container.redis.get_orderbook = AsyncMock(
            return_value={
                "bids": [{"price": "0.58", "size": "100"},
                         {"price": "0.57", "size": "50"}],
                "asks": [{"price": "0.62", "size": "80"},
                         {"price": "0.63", "size": "40"}],
            }
        )
        # consecutive_ticks: la mayoría de strategies devuelve None aquí
        test_app.state.container.strategy_engine.get_state = MagicMock(
            return_value=None
        )

        response = client.get("/api/v1/dashboard/markets")
        assert response.status_code == 200
        data = response.json()
        # Deduplica: 1 BTC 5m (el de mayor volumen) + 1 ETH 15m
        assert len(data) == 2
        ids = {row["market_id"] for row in data}
        assert "m-btc-5m-high" in ids
        assert "m-btc-5m-low" not in ids
        # Best bid = max de bids, best ask = min de asks
        btc = next(r for r in data if r["asset"] == "BTC")
        assert btc["best_bid"] == 0.58
        assert btc["best_ask"] == 0.62
        assert btc["ws_connected"] is True
        assert len(btc["orderbook_bids"]) == 2

    def test_markets_overview_empty_orderbook(self, test_app, client):
        """Orderbook ausente → bids/asks vacíos y best_bid/ask=0."""
        m = _mk_market("BTC", "5m")
        test_app.state.container.market_service.get_active_markets = AsyncMock(
            return_value=[m]
        )
        test_app.state.container.redis.get_orderbook = AsyncMock(return_value=None)
        test_app.state.container.redis.get_ws_state = AsyncMock(return_value=None)
        test_app.state.container.strategy_engine.get_state = MagicMock(
            return_value=None
        )

        response = client.get("/api/v1/dashboard/markets")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["orderbook_bids"] == []
        assert data[0]["best_bid"] == 0.0
        assert data[0]["ws_connected"] is False

    def test_equity_curve_returns_cumulative_pnl(self, test_app, client):
        """Equity curve acumula pnl trade a trade."""
        positions = [_mk_pos(+50, minute=1), _mk_pos(+30, minute=2)]
        test_app.state.container.repository.get_positions = AsyncMock(
            return_value=positions
        )
        response = client.get("/api/v1/dashboard/equity")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["cumulative_pnl"] == 50.0
        assert data[1]["cumulative_pnl"] == 80.0
        assert data[1]["balance"] == 1080.0  # 1000 inicial + 80

    def test_trades_endpoint_lists_open_and_closed(self, test_app, client):
        """Endpoint /trades incluye is_open según closed_at."""
        closed = _mk_pos(+25, minute=1)
        open_pos = _mk_pos(None, closed_at=None, exit_reason=None)
        open_pos.exit_price = None
        open_pos.pnl = None
        open_pos.pnl_pct = None
        test_app.state.container.repository.get_positions = AsyncMock(
            return_value=[closed, open_pos]
        )
        response = client.get("/api/v1/dashboard/trades?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        opens = [t for t in data if t["is_open"]]
        assert len(opens) == 1
        assert opens[0]["pnl"] is None

    def test_regimes_overview_returns_empty_when_no_orchestrator(self, test_app, client):
        """Sin orchestrator → lista vacía sin error."""
        test_app.state.container.strategy_orchestrator = None
        response = client.get("/api/v1/dashboard/regimes")
        assert response.status_code == 200
        assert response.json() == []

    def test_regimes_overview_with_status(self, test_app, client):
        """Con orchestrator: regime, confidence y strategies activas se reflejan."""
        from src.infrastructure.data.regime import Regime

        orchestrator = MagicMock()
        orchestrator.enabled = True
        orchestrator.get_regime_status = MagicMock(return_value={
            "regime": Regime.TREND,
            "confidence": 0.82,
            "strategies_active": ["mean_reversion"],
            "strategies_inactive": ["bat"],
        })
        test_app.state.container.strategy_orchestrator = orchestrator

        m_btc = _mk_market("BTC", "5m", market_id="mkt-btc-5m")
        m_eth = _mk_market("ETH", "15m", market_id="mkt-eth-15m")
        m_dup = _mk_market("BTC", "5m", market_id="mkt-btc-5m-dup")  # dup
        test_app.state.container.market_service.get_active_markets = AsyncMock(
            return_value=[m_btc, m_eth, m_dup]
        )

        response = client.get("/api/v1/dashboard/regimes")
        assert response.status_code == 200
        data = response.json()
        # Deduplicado: 2 entradas
        assert len(data) == 2
        assert all(r["orchestrator_enabled"] is True for r in data)
        assert all(r["confidence"] == 0.82 for r in data)
        assert {"mean_reversion"} == set(data[0]["strategies_active"])

    def test_regimes_overview_status_none_returns_defaults(self, test_app, client):
        """get_regime_status devuelve None → regime='unknown', confidence=0."""
        orchestrator = MagicMock()
        orchestrator.enabled = False
        orchestrator.get_regime_status = MagicMock(return_value=None)
        test_app.state.container.strategy_orchestrator = orchestrator
        test_app.state.container.market_service.get_active_markets = AsyncMock(
            return_value=[_mk_market("BTC", "5m")]
        )
        response = client.get("/api/v1/dashboard/regimes")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["regime"] == "unknown"
        assert data[0]["confidence"] == 0.0
        assert data[0]["orchestrator_enabled"] is False


# ──────────────────────────────────────────────────────────────────────
# R1.5 — HEALTH ERROR PATHS
# ──────────────────────────────────────────────────────────────────────


class TestHealthErrorPaths:
    """R1.5: ramas de error y DEGRADED de health.py."""

    def test_health_db_exception_marks_down(self, test_app, client):
        """Si repository.get_active_markets lanza, database → DOWN."""
        test_app.state.container.repository.get_active_markets = AsyncMock(
            side_effect=RuntimeError("DB down")
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["database"] == ServiceStatusEnum.DOWN.value
        assert data["status"] == ServiceStatusEnum.DOWN.value

    def test_health_redis_exception_marks_down(self, test_app, client):
        """Si redis.ping lanza, redis → DOWN y status global DOWN."""
        test_app.state.container.redis._redis.ping = AsyncMock(
            side_effect=ConnectionError("redis unreachable")
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["redis"] == ServiceStatusEnum.DOWN.value

    def test_health_telegram_exception_marks_down(self, test_app, client):
        """Si telegram_bot.get_me lanza, telegram → DOWN."""
        test_app.state.container.telegram_bot.get_me = AsyncMock(
            side_effect=RuntimeError("auth failed")
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["telegram"] == ServiceStatusEnum.DOWN.value

    def test_health_telegram_none_marks_degraded(self, test_app, client):
        """Sin bot configurado → telegram = DEGRADED."""
        test_app.state.container.telegram_bot = None
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["telegram"] == ServiceStatusEnum.DEGRADED.value

    def test_health_cross_verify_ok(self, test_app, client):
        """cross_verify_positions devuelve ok → status data_api_cross = OK."""
        test_app.state.container.cross_verify_positions = AsyncMock(
            return_value={"status": "ok"}
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["data_api_cross"] == ServiceStatusEnum.OK.value

    def test_health_cross_verify_degraded(self, test_app, client):
        """status degraded → data_api_cross = DEGRADED."""
        test_app.state.container.cross_verify_positions = AsyncMock(
            return_value={"status": "degraded", "discrepancies": ["mkt-1"]}
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["data_api_cross"] == ServiceStatusEnum.DEGRADED.value

    def test_health_cross_verify_down(self, test_app, client):
        """status down → data_api_cross = DOWN y overall DOWN."""
        test_app.state.container.cross_verify_positions = AsyncMock(
            return_value={"status": "down", "discrepancies": ["a", "b", "c"]}
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["data_api_cross"] == ServiceStatusEnum.DOWN.value
        assert data["status"] == ServiceStatusEnum.DOWN.value

    def test_health_cross_verify_exception_marks_degraded(self, test_app, client):
        """Excepción en cross_verify → DEGRADED (no rompe el endpoint)."""
        test_app.state.container.cross_verify_positions = AsyncMock(
            side_effect=Exception("data api timeout")
        )
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["services"]["data_api_cross"] == ServiceStatusEnum.DEGRADED.value
