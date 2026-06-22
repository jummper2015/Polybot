// ── Dashboard Summary ─────────────────────────────────────────────────
export interface DashboardSummary {
  balance: number;
  initial_balance: number;
  total_pnl_usdc: number;
  total_pnl_pct: number;
  open_positions: number;
  closed_positions: number;
  total_trades: number;
  win_rate: number;
  profit_factor: number;
  max_drawdown_pct: number;
  active_markets: number;
  bot_running: boolean;
  trading_mode: "paper" | "real";
  drawdown_pct: number;
  updated_at: string;
}

// ── Equity Point ──────────────────────────────────────────────────────
export interface EquityPoint {
  timestamp: string;
  cumulative_pnl: number;
  trade_pnl: number;
  balance: number;
  exit_reason: string;
}

// ── Orderbook Level ───────────────────────────────────────────────────
export interface OrderbookLevel {
  price: number;
  size: number;
}

// ── Market Overview ───────────────────────────────────────────────────
export interface MarketOverview {
  market_id: string;
  asset: string;
  window: string;
  yes_price: number;
  best_bid: number;
  best_ask: number;
  spread: number;
  volume_24h: number;
  ws_connected: boolean;
  consecutive_ticks: number;
  orderbook_bids: OrderbookLevel[];
  orderbook_asks: OrderbookLevel[];
}

// ── Recent Trade ──────────────────────────────────────────────────────
export interface RecentTrade {
  id: string;
  asset: string;
  window: string;
  side: string;
  amount: number;
  entry_price: number;
  exit_price: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  mode: string;
  exit_reason: string | null;
  opened_at: string;
  closed_at: string | null;
  is_open: boolean;
}

// ── Position (from /api/v1/positions) ─────────────────────────────────
export interface Position {
  id: string;
  asset: string;
  window: string;
  side: string;
  amount: number;
  entry_price: number;
  exit_price: number | null;
  current_price: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  mode: string;
  exit_reason: string | null;
  opened_at: string;
  closed_at: string | null;
  is_open: boolean;
}

// ── Order (from /api/v1/orders) ──────────────────────────────────────
export interface Order {
  id: string;
  market_id: string;
  asset: string;
  window: string;
  side: string;
  amount: number;
  price: number;
  status: string;
  mode: string;
  idempotency_key: string | null;
  retry_count: number;
  created_at: string;
  updated_at: string;
}

// ── Health Check ──────────────────────────────────────────────────────
export type ServiceStatus = "OK" | "DEGRADED" | "DOWN";

export interface HealthResponse {
  status: ServiceStatus;
  version: string;
  mode: string;
  services: Record<string, ServiceStatus>;
}

// ── Positions / Orders List Responses ────────────────────────────────
export interface PositionsListResponse {
  positions: Position[];
  total: number;
}

export interface OrdersListResponse {
  orders: Order[];
  total: number;
}

// ── Regime (P11.1) ────────────────────────────────────────────────────
export type RegimeType = 'trend' | 'chop' | 'panic' | 'illiquid' | 'event_driven';

export interface RegimeInfo {
  asset: string;
  window: string;
  regime: RegimeType;
  confidence: number;
  strategies_active: string[];
  strategies_inactive: string[];
  orchestrator_enabled: boolean;
}

// ── Quant Metrics (R1.3-dashboard) ────────────────────────────────────
export interface ExitReasonStats {
  reason: string;
  count: number;
  pct_of_trades: number;
  total_pnl: number;
  avg_pnl: number;
  win_rate: number;
}

export interface RegimeStatsRow {
  regime: string;
  count: number;
  total_pnl: number;
  win_rate: number;
}

export interface QuantMetrics {
  total_trades: number;
  expectancy_usdc: number;
  expectancy_pct: number;
  profit_factor: number;
  sharpe_estimate: number;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  best_trade: number;
  worst_trade: number;
  avg_winner: number;
  avg_loser: number;
  avg_duration_ticks: number;
  best_exit_reason: string;
  worst_exit_reason: string;
  best_regime: string;
  worst_regime: string;
  by_exit_reason: ExitReasonStats[];
  by_regime: RegimeStatsRow[];
  updated_at: string;
}

// ── Risk Engine activity (R1.3-dashboard) ─────────────────────────────
export interface RiskRuleStat {
  rule: string;
  deny_count: number;
  priority: number;
}

export interface RiskActivity {
  rules: RiskRuleStat[];
  allow_count: number;
  deny_count: number;
  drawdown_pct: number;
  exposure_pct: number;
  mode: string;
  updated_at: string;
}

// ── Event Detector activity (R1.3-dashboard, P11.4) ──────────────────
export interface EventTypeCount {
  asset: string;
  event_type: string;
  severity: string;
  count: number;
}

export interface EventActionCount {
  asset: string;
  action: string;
  count: number;
}

export interface ActiveHalt {
  asset: string;
  market_id: string;
}

export interface EventActivity {
  by_type: EventTypeCount[];
  by_action: EventActionCount[];
  halt_entries: number;
  active_halts: ActiveHalt[];
  updated_at: string;
}
