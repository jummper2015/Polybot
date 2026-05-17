# src/infrastructure/observability/metrics.py
# Registro COMPLETO y consolidado de todas las métricas del sistema

from prometheus_client import (
    Counter, Gauge, Histogram, Summary,
    CollectorRegistry, REGISTRY,
)

# ══════════════════════════════════════════════════════════════
# WEBSOCKET
# ══════════════════════════════════════════════════════════════
WS_CONNECTED = Gauge(
    "polymarket_ws_connected",
    "WebSocket connection status per market (1=up, 0=down)",
    ["market_id"],
)
WS_RECONNECTS = Counter(
    "polymarket_ws_reconnects_total",
    "WebSocket reconnection attempts per market",
    ["market_id"],
)
WS_TICKS_RECEIVED = Counter(
    "polymarket_ws_ticks_total",
    "Market ticks received per market",
    ["market_id"],
)
WS_ERRORS = Counter(
    "polymarket_ws_errors_total",
    "WebSocket errors per market",
    ["market_id"],
)

# ══════════════════════════════════════════════════════════════
# DISCOVERY
# ══════════════════════════════════════════════════════════════
MARKETS_DISCOVERED = Counter(
    "polymarket_markets_discovered_total",
    "Markets discovered per asset and window",
    ["asset", "window"],
)
MARKETS_ACTIVE = Gauge(
    "polymarket_markets_active",
    "Currently active markets per asset and window",
    ["asset", "window"],
)

# ══════════════════════════════════════════════════════════════
# CICLO DE TRADING
# ══════════════════════════════════════════════════════════════
CYCLE_DURATION = Histogram(
    "polymarket_cycle_duration_seconds",
    "Duration of each market cycle in seconds",
    ["asset", "window"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)
CYCLE_ERRORS = Counter(
    "polymarket_cycle_errors_total",
    "Total errors in market cycles",
)
SIGNALS_GENERATED = Counter(
    "polymarket_signals_total",
    "Trading signals generated per type and asset",
    ["type", "asset"],
)

# ══════════════════════════════════════════════════════════════
# HTTP CLIENT
# ══════════════════════════════════════════════════════════════
HTTP_REQUEST_DURATION = Histogram(
    "polymarket_http_request_duration_seconds",
    "HTTP request duration per endpoint",
    ["endpoint"],
    buckets=[0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
)

# ══════════════════════════════════════════════════════════════
# ÓRDENES Y PNL
# ══════════════════════════════════════════════════════════════
ORDERS_EXECUTED = Counter(
    "polymarket_orders_total",
    "Total orders executed per mode and side",
    ["mode", "side"],
)
PNL_GAUGE = Gauge(
    "polymarket_pnl_usdc",
    "Current total PnL in USDC per mode",
    ["mode"],
)
PNL_PER_TRADE = Histogram(
    "polymarket_pnl_per_trade_usdc",
    "PnL per closed trade in USDC",
    ["mode", "exit_reason"],
    buckets=[-5.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 5.0, 10.0],
)
SLIPPAGE_OBSERVED = Histogram(
    "polymarket_slippage_observed",
    "Slippage observed per order (fill vs target)",
    ["mode"],
    buckets=[0.0, 0.001, 0.005, 0.01, 0.02, 0.05],
)

# ══════════════════════════════════════════════════════════════
# PAPER TRADING
# ══════════════════════════════════════════════════════════════
PAPER_BALANCE_GAUGE = Gauge(
    "polymarket_paper_balance_usdc",
    "Current paper trading virtual balance in USDC",
)
PAPER_POSITIONS_OPEN = Gauge(
    "polymarket_paper_positions_open",
    "Number of currently open paper trading positions",
)

# ══════════════════════════════════════════════════════════════
# REAL TRADING
# ══════════════════════════════════════════════════════════════
REAL_ORDER_RETRIES = Counter(
    "polymarket_real_order_retries_total",
    "Real trading order retry attempts",
    ["market_id", "operation"],
)
REAL_ORDER_ERRORS = Counter(
    "polymarket_real_order_errors_total",
    "Real trading order final failures",
    ["market_id"],
)

# ══════════════════════════════════════════════════════════════
# STRATEGY ENGINE
# ══════════════════════════════════════════════════════════════
STRATEGY_CYCLES = Counter(
    "polymarket_strategy_cycles_total",
    "Strategy evaluation cycles per strategy and result",
    ["strategy", "result"],
)
STRATEGY_ERRORS = Counter(
    "polymarket_strategy_errors_total",
    "Errors inside strategy methods per strategy",
    ["strategy"],
)
FILTER_REJECTIONS = Counter(
    "polymarket_filter_rejections_total",
    "Filter rejections per filter name",
    ["filter_name"],
)
BAT_CONSECUTIVE_TICKS = Gauge(
    "bat_consecutive_ticks",
    "Consecutive ticks above threshold per market",
    ["market_id", "asset"],
)
BAT_ENTRY_CONFIDENCE = Histogram(
    "bat_entry_confidence",
    "Confidence score at entry signal generation",
    ["market_id", "asset"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# ══════════════════════════════════════════════════════════════
# RISK ENGINE
# ══════════════════════════════════════════════════════════════
RISK_DECISIONS = Counter(
    "polymarket_risk_decisions_total",
    "Risk decisions by result, rule and mode",
    ["result", "rule", "mode"],
)
RISK_RULE_TRIGGERED = Counter(
    "polymarket_risk_rule_triggered_total",
    "How many times each rule triggered a DENY",
    ["rule"],
)
RISK_DRAWDOWN_GAUGE = Gauge(
    "polymarket_risk_drawdown_pct",
    "Current daily drawdown percentage",
    ["mode"],
)
RISK_EXPOSURE_GAUGE = Gauge(
    "polymarket_risk_exposure_pct",
    "Current total portfolio exposure percentage",
    ["mode"],
)

# ══════════════════════════════════════════════════════════════
# SEGURIDAD
# ══════════════════════════════════════════════════════════════
SECURITY_GUARDRAIL_TRIGGERED = Counter(
    "polymarket_security_guardrail_triggered_total",
    "Security guardrails triggered per check type",
    ["check"],
)
SECURITY_RATE_LIMIT_BLOCKED = Counter(
    "polymarket_security_rate_limit_blocked_total",
    "Real orders blocked by rate limiter",
)
SECURITY_AUDIT_LOGS_WRITTEN = Counter(
    "polymarket_security_audit_logs_total",
    "Audit log entries written per action",
    ["action"],
)

# ══════════════════════════════════════════════════════════════
# SISTEMA / API
# ══════════════════════════════════════════════════════════════
API_REQUEST_COUNT = Counter(
    "polymarket_api_requests_total",
    "Total API requests per endpoint and method",
    ["endpoint", "method", "status"],
)
API_REQUEST_DURATION = Histogram(
    "polymarket_api_request_duration_seconds",
    "API request duration per endpoint",
    ["endpoint", "method"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)
DB_QUERY_DURATION = Histogram(
    "polymarket_db_query_duration_seconds",
    "Database query duration per operation",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5],
)
REDIS_OPERATION_DURATION = Histogram(
    "polymarket_redis_operation_duration_seconds",
    "Redis operation duration per operation type",
    ["operation"],
    buckets=[0.0001, 0.001, 0.005, 0.01, 0.05],
)
BOT_UPTIME = Gauge(
    "polymarket_bot_uptime_seconds",
    "Seconds since the bot started",
)