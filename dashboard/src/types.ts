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

// ── Market Overview ───────────────────────────────────────────────────
export interface MarketOverview {
  market_id: string;
  asset: string;
  window: string;
  yes_price: number;
  spread: number;
  volume_24h: number;
  ws_connected: boolean;
  consecutive_ticks: number;
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
