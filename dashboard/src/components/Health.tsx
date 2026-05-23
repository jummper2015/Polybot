import React from 'react';
import { formatUSDC, formatPct } from '../hooks/useApi';
import type { DashboardSummary } from '../types';
import { KpiCard, StatusBadge, Spinner } from './Common';

interface SummaryProps {
  summary: DashboardSummary | null;
  loading: boolean;
}

export const SystemHealth: React.FC<SummaryProps> = ({ summary, loading }) => {
  if (loading && !summary) return <div className="health-row"><Spinner /></div>;
  if (!summary) return null;

  return (
    <section className="section">
      <div className="section-header">
        <h2>🟢 System Health</h2>
        <span className="update-badge">⏱️ {new Date().toLocaleTimeString()}</span>
      </div>
      <div className="health-row">
        <div className="health-item">
          <span className="health-dot ok" />
          <span>Bot</span>
          <StatusBadge label={summary.bot_running ? 'RUNNING' : 'STOPPED'} variant={summary.bot_running ? 'running' : 'stopped'} size="sm" />
        </div>
        <div className="health-item">
          <span className="health-dot ok" />
          <span>Mode</span>
          <StatusBadge label={summary.trading_mode.toUpperCase()} variant={summary.trading_mode as 'paper' | 'real'} size="sm" />
        </div>
        <div className="health-item">
          <span className={`health-dot ${summary.bot_running ? 'ok' : 'down'}`} />
          <span>Markets</span>
          <strong>{summary.active_markets}</strong>
        </div>
        <div className="health-item">
          <span className={`health-dot ${summary.drawdown_pct > 0.1 ? 'down' : summary.drawdown_pct > 0.05 ? 'degraded' : 'ok'}`} />
          <span>Drawdown</span>
          <strong className={summary.drawdown_pct > 0.08 ? 'negative' : summary.drawdown_pct > 0.05 ? 'neutral' : 'positive'}>
            {formatPct(summary.drawdown_pct)}
          </strong>
        </div>
        <div className="health-item">
          <span className="health-dot ok" />
          <span>Trades</span>
          <strong>{summary.total_trades}</strong>
        </div>
        <div className="health-item">
          <span className={`health-dot ${summary.open_positions > 0 ? 'ok' : ''}`} />
          <span>Positions</span>
          <strong>{summary.open_positions} open</strong>
        </div>
      </div>
    </section>
  );
};

// ── KPI Row ───────────────────────────────────────────────────────────
export const KpiRow: React.FC<SummaryProps> = ({ summary, loading }) => {
  if (loading && !summary) {
    return (
      <section className="kpi-grid">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="kpi-card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 100 }}>
            <Spinner />
          </div>
        ))}
      </section>
    );
  }
  if (!summary) return null;

  const balDiff = summary.balance - summary.initial_balance;
  const pfTrend = summary.profit_factor >= 1.5 ? 'up' : summary.profit_factor >= 1.0 ? 'neutral' : 'down';
  const ddTrend = summary.max_drawdown_pct < 0.05 ? 'up' : summary.max_drawdown_pct < 0.08 ? 'neutral' : 'down';
  const pnlTrend = summary.total_pnl_usdc > 0 ? 'up' : summary.total_pnl_usdc < 0 ? 'down' : 'neutral';

  return (
    <section className="kpi-grid">
      <KpiCard
        label="💰 Balance"
        value={formatUSDC(summary.balance)}
        sub={`${formatUSDC(balDiff)} desde inicio`}
        trend={balDiff >= 0 ? 'up' : 'down'}
        valueClassName={balDiff >= 0 ? 'positive' : 'negative'}
      />
      <KpiCard
        label="📈 PnL Total"
        value={formatUSDC(summary.total_pnl_usdc)}
        sub={`${formatPct(summary.total_pnl_pct)} | ${summary.closed_positions} trades`}
        trend={pnlTrend}
        valueClassName={summary.total_pnl_usdc > 0 ? 'positive' : summary.total_pnl_usdc < 0 ? 'negative' : ''}
      />
      <KpiCard
        label="🎯 Win Rate"
        value={formatPct(summary.win_rate)}
        sub={`${summary.closed_positions} cerrados`}
        trend={summary.win_rate > 0.45 ? 'up' : summary.win_rate > 0.35 ? 'neutral' : 'down'}
      />
      <KpiCard
        label="⚡ Profit Factor"
        value={summary.profit_factor.toFixed(2)}
        sub="ratio ganancias/pérdidas"
        trend={pfTrend}
        valueClassName={pfTrend === 'up' ? 'positive' : pfTrend === 'down' ? 'negative' : ''}
      />
      <KpiCard
        label="📉 Max Drawdown"
        value={formatPct(summary.max_drawdown_pct)}
        sub={`actual: ${formatPct(summary.drawdown_pct)}`}
        trend={ddTrend}
        valueClassName={ddTrend === 'up' ? 'positive' : ddTrend === 'down' ? 'negative' : ''}
      />
      <KpiCard
        label="📊 Posiciones"
        value={String(summary.open_positions)}
        sub={`${summary.active_markets} mercados activos`}
        trend={summary.open_positions > 0 ? 'up' : 'neutral'}
      />
    </section>
  );
};
