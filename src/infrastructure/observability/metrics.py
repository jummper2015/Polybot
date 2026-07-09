# src/infrastructure/observability/metrics.py
# Registro COMPLETO y consolidado de todas las métricas del sistema

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
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
SLIPPAGE_ESTIMATED = Histogram(
    "polybot_slippage_estimated",
    "Slippage estimated by SlippageEngine before execution",
    ["mode", "side"],
    buckets=[0.0, 0.001, 0.005, 0.01, 0.02, 0.05, 0.10],
)
SLIPPAGE_ACTUAL_VS_ESTIMATED = Histogram(
    "polybot_slippage_ratio_actual_estimated",
    "Ratio of actual slippage to estimated slippage (1.0 = perfect)",
    ["mode", "side"],
    buckets=[0.1, 0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0, 5.0],
)
SLIPPAGE_CALIBRATION = Gauge(
    "polybot_slippage_calibration_multiplier",
    "Current SlippageTracker calibration multiplier (1.0 = model accurate)",
    ["mode"],
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

# ─────────────────────────────────────────────────────────────────────────────────
# CTF ON-CHAIN REDEEM (R2.0-redeem-impl F1, RFC_approved 2026-06-25 §13)
# Métricas para el flujo de redención via web3.py 7.16.0 hacia el
# Thin Collateral Adapter (0x93070a...) en Polygon Mainnet.
# ─────────────────────────────────────────────────────────────────────────────────
REDEEM_GAS_USED = Histogram(
    "polybot_redeem_gas_used",
    "Gas consumido por tx de redeem CTF (gas units)",
    ["proxy"],
    buckets=[50_000, 100_000, 200_000, 350_000, 500_000, 750_000, 1_000_000],
)
REDEEM_PUSD_RECEIVED = Counter(
    "polybot_redeem_pusd_received_total",
    "pUSD acreditado al POLY_PROXY tras redeem confirmado (pUSD)",
    ["proxy"],
)
REDEEM_TX_MINING_SECONDS = Histogram(
    "polybot_redeem_tx_mining_seconds",
    "Tiempo entre submit en mempool y minería del bloque",
    buckets=[1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0],
)
REDEEM_TX_FINALITY_SECONDS = Histogram(
    "polybot_redeem_tx_finality_seconds",
    "Tiempo entre submit y finality práctica (64 confirmaciones @ Polygon)",
    buckets=[30.0, 60.0, 120.0, 240.0, 480.0, 960.0, 1800.0],
)
REDEEM_FAILURES_REASON = Counter(
    "polybot_redeem_failures_total",
    "Total de fallos de redeem categorizados por reason",
    ["reason"],
)
REDEEM_REPLACEMENTS = Counter(
    "polybot_redeem_tx_replacements_total",
    "Total de tx replacements (mismo nonce + gas bumped)",
)
REDEEM_PROXY_MATIC_BALANCE_GAUGE = Gauge(
    "polybot_redeem_proxy_matic_balance",
    "Balance MATIC del POLY_PROXY (wei)",
    ["wallet"],
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
# MEAN REVERSION STRATEGY
# ══════════════════════════════════════════════════════════════
MR_ZSCORE = Gauge(
    "mr_zscore",
    "Current z-score for mean reversion per market",
    ["market_id", "asset"],
)
MR_ENTRY_CONFIDENCE = Histogram(
    "mr_entry_confidence",
    "Confidence score at mean reversion entry signal generation",
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
CIRCUIT_BREAKER_STATE = Gauge(
    "polymarket_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
)

# ══════════════════════════════════════════════════════════════
# GRACEFUL DEGRADATION WS → REST
# ══════════════════════════════════════════════════════════════
MARKET_DATA_SOURCE = Gauge(
    "polymarket_market_data_source",
    "Current data source per market (2=ws, 1=rest)",
    ["market_id"],
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

# ══════════════════════════════════════════════════════════════
# DATA RECORDING (P8.1)
# ══════════════════════════════════════════════════════════════
RECORDING_TICKS_TOTAL = Counter(
    "polybot_recording_ticks_total",
    "Total ticks recorded per asset during live data recording",
    ["asset"],
)
RECORDING_WS_RECONNECTS = Counter(
    "polybot_recording_ws_reconnects_total",
    "WebSocket reconnection attempts during live data recording per asset",
    ["asset"],
)
RECORDING_STORAGE_SIZE_BYTES = Gauge(
    "polybot_recording_storage_size_bytes",
    "Total size of Parquet data files on disk (all assets)",
)
RECORDING_UPTIME_SECONDS = Gauge(
    "polybot_recording_uptime_seconds",
    "Seconds since the data recording process started",
)
RECORDING_MARKETS_ACTIVE = Gauge(
    "polybot_recording_markets_active",
    "Number of markets currently being recorded",
)

# ══════════════════════════════════════════════════════════════
# QUEUE POSITION MODELING (P9.3)
# ══════════════════════════════════════════════════════════════
QUEUE_MAKER_FILL_PROBABILITY = Histogram(
    "polybot_queue_maker_fill_probability",
    "Estimated P(fill) for maker orders within wait window",
    ["side", "regime"],
    buckets=[0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0],
)
QUEUE_MAKER_EXPECTED_TIME = Histogram(
    "polybot_queue_maker_expected_time_seconds",
    "Expected time to fill for maker orders",
    ["side"],
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)
QUEUE_ADVERSE_SELECTION_BPS = Histogram(
    "polybot_queue_adverse_selection_bps",
    "Estimated adverse selection cost in basis points",
    ["side", "regime"],
    buckets=[0.0, 1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0],
)
QUEUE_MAKER_VS_TAKER_DECISIONS = Counter(
    "polybot_queue_maker_vs_taker_decisions_total",
    "Maker-vs-taker decisions by recommended mode",
    ["mode", "side"],
)
QUEUE_MAKER_COST_RATIO = Histogram(
    "polybot_queue_maker_cost_ratio",
    "Ratio maker_cost / taker_cost (< 1.0 = maker cheaper)",
    ["side"],
    buckets=[0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 1.0, 1.25, 1.5, 2.0, 5.0],
)
QUEUE_MAKER_SAVINGS_PCT = Histogram(
    "polybot_queue_maker_savings_pct",
    "Estimated savings from choosing MAKER over TAKER (percentage)",
    ["side"],
    buckets=[0.0, 1.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0],
)
QUEUE_TURNOVER_VOLUME_SEC = Histogram(
    "polybot_queue_turnover_volume_sec",
    "Estimated taker volume per second (USDC/sec)",
    ["asset"],
    buckets=[0.0, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)
QUEUE_CONFIDENCE = Histogram(
    "polybot_queue_confidence",
    "Confidence in queue position estimate (0.0-1.0)",
    ["side"],
    buckets=[0.0, 0.1, 0.3, 0.5, 0.7, 0.8, 0.9, 1.0],
)

# ══════════════════════════════════════════════════════════════
# DATA API — CROSS-VERIFICATION (Real Trading)
# ══════════════════════════════════════════════════════════════
POSITION_CROSS_VERIFY_DISCREPANCIES = Gauge(
    "polybot_position_cross_verify_discrepancies",
    "Number of position discrepancies between local DB and Data API. -1 = query failed",
)

# ══════════════════════════════════════════════════════════════
# REGIME AWARENESS (P11.1)
# ══════════════════════════════════════════════════════════════
REGIME_CURRENT = Gauge(
    "polybot_regime_current",
    "Current market regime per asset/window (1 = active, 0 = inactive)",
    ["asset", "window", "regime"],
)
REGIME_CONFIDENCE = Gauge(
    "polybot_regime_confidence",
    "Confidence of the current regime classification (0.0-1.0)",
    ["asset", "window", "regime"],
)
REGIME_CLASSIFICATIONS = Counter(
    "polybot_regime_classifications_total",
    "Total number of regime classifications per regime and asset",
    ["asset", "window", "regime"],
)
STRATEGY_SKIPPED_BY_REGIME = Counter(
    "polybot_strategy_skipped_by_regime_total",
    "Times a strategy was skipped due to regime incompatibility",
    ["strategy", "regime"],
)
STRATEGY_ACTIVE_IN_REGIME = Counter(
    "polybot_strategy_active_in_regime_total",
    "Times a strategy was evaluated in a compatible regime",
    ["strategy", "regime"],
)
REGIME_ORCHESTRATOR_ENABLED = Gauge(
    "polybot_regime_orchestrator_enabled",
    "Whether the RegimeAwareOrchestrator is active (1=enabled, 0=disabled)",
)

# ══════════════════════════════════════════════════════════════
# ENSEMBLE SIGNAL ENGINE (P11.2)
# ══════════════════════════════════════════════════════════════
ENSEMBLE_SIGNALS = Counter(
    "polybot_ensemble_signals_total",
    "Ensemble signal outcomes (buy_yes, conflict_hold, etc.)",
    ["outcome"],
)
ENSEMBLE_CONFLICTS = Counter(
    "polybot_ensemble_conflicts_total",
    "Times ensemble detected conflicting signals (BUY vs SELL)",
)
ENSEMBLE_AGREEMENT_BONUS = Counter(
    "polybot_ensemble_agreement_bonus_total",
    "Times agreement bonus was applied (2+ strategies agreed)",
)
ENSEMBLE_CONTRIBUTIONS = Counter(
    "polybot_ensemble_contributions_total",
    "Per-strategy contribution count to ensemble signals",
    ["strategy"],
)
ENSEMBLE_WEIGHTS = Gauge(
    "polybot_ensemble_strategy_weight",
    "Current ensemble weight per strategy",
    ["strategy"],
)

# ══════════════════════════════════════════════════════════════
# LIQUIDITY-AWARE TRADING (P11.3)
# ══════════════════════════════════════════════════════════════
LIQUIDITY_MULTIPLIER = Gauge(
    "polybot_liquidity_multiplier",
    "Current liquidity multiplier applied to position size (0.25-1.0)",
    ["asset", "side"],
)
LIQUIDITY_DEPTH_COVERAGE = Gauge(
    "polybot_liquidity_depth_coverage",
    "Ratio of total L1 depth to order size (higher = more liquidity)",
    ["asset", "side"],
)
LIQUIDITY_SIZE_REDUCTIONS = Counter(
    "polybot_liquidity_size_reductions_total",
    "Total times position size was reduced due to low liquidity",
    ["asset", "reason"],
)
LIQUIDITY_SPREAD_FACTOR = Gauge(
    "polybot_liquidity_spread_factor",
    "Current spread penalty factor (0.7-1.0, lower = worse spread)",
    ["asset", "side"],
)
LIQUIDITY_VOLUME_FACTOR = Gauge(
    "polybot_liquidity_volume_factor",
    "Current volume penalty factor (0.6-1.0, lower = lower volume)",
    ["asset", "side"],
)

# ══════════════════════════════════════════════════════════════
# EVENT-DRIVEN TRADING (P11.4)
# ══════════════════════════════════════════════════════════════
EVENT_DETECTED = Counter(
    "polybot_event_detected_total",
    "Market events detected by type and severity",
    ["asset", "event_type", "severity"],
)
EVENT_RESPONSE = Counter(
    "polybot_event_response_total",
    "Trading responses to market events by action",
    ["asset", "action"],
)
EVENT_HALT_ENTRIES = Counter(
    "polybot_event_halt_entries_total",
    "Entries blocked due to event HALT response",
    ["asset"],
)
EVENT_ACTIVE = Gauge(
    "polybot_event_active",
    "Whether a market has an active blocking event (1=blocking, 0=clear)",
    ["asset", "market_id"],
)
