import React from 'react';
import { Activity, TrendingUp, TrendingDown, Repeat, Clock, Award } from 'lucide-react';
import { usePolling, formatUSDC, formatPct } from '../hooks/useApi';
import type { QuantMetrics } from '../types';
import { Spinner } from './Common';

const COLORS = {
  positive: '#22c55e',
  negative: '#ef4444',
  neutral:  '#8892a4',
};

const Row: React.FC<{ icon: React.ReactNode; label: string; value: string; tone?: 'positive' | 'negative' | 'neutral' }> = ({ icon, label, value, tone = 'neutral' }) => (
  <div className="qm-row">
    <span className="qm-icon" style={{ color: COLORS[tone] }}>{icon}</span>
    <span className="qm-label">{label}</span>
    <span className={`qm-value ${tone}`}>{value}</span>
  </div>
);

export const QuantMetricsCard: React.FC = () => {
  const { data, loading } = usePolling<QuantMetrics>('/dashboard/quant-metrics', 10_000);

  if (loading && !data) {
    return (
      <div className="table-card">
        <div className="section-header"><h2>📐 Quant Metrics</h2></div>
        <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}>
          <Spinner />
        </div>
      </div>
    );
  }
  if (!data) return null;

  const hasTrades = data.total_trades > 0;

  // Si no hay trades, render compacto explicativo (no oculta el panel — visibilidad sobre el estado actual).
  if (!hasTrades) {
    return (
      <div className="table-card">
        <div className="section-header">
          <h2><Activity size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />Quant Metrics</h2>
          <span className="count-badge muted">0 closed trades</span>
        </div>
        <div className="empty-row" style={{ padding: 20, textAlign: 'center' }}>
          <span className="text-muted">
            Sin trades cerrados todavía. Sharpe, expectancy y attribution aparecen aquí cuando el bot empiece a cerrar posiciones.
          </span>
        </div>
      </div>
    );
  }

  const sharpeTone = data.sharpe_estimate > 0.8 ? 'positive' : data.sharpe_estimate < 0 ? 'negative' : 'neutral';
  const pfTone = data.profit_factor > 1.2 ? 'positive' : data.profit_factor < 1 ? 'negative' : 'neutral';
  const expTone = data.expectancy_usdc > 0 ? 'positive' : data.expectancy_usdc < 0 ? 'negative' : 'neutral';

  return (
    <div className="table-card">
      <div className="section-header">
        <h2><Activity size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />Quant Metrics</h2>
        <span className="count-badge">{data.total_trades} trades</span>
      </div>

      <div className="qm-grid">
        <Row
          icon={<TrendingUp size={16} />}
          label="Sharpe (est.)"
          value={data.sharpe_estimate.toFixed(3)}
          tone={sharpeTone}
        />
        <Row
          icon={<Award size={16} />}
          label="Expectancy"
          value={`${formatUSDC(data.expectancy_usdc)} (${formatPct(data.expectancy_pct)})`}
          tone={expTone}
        />
        <Row
          icon={<TrendingUp size={16} />}
          label="Profit Factor"
          value={data.profit_factor >= 100 ? '99+' : data.profit_factor.toFixed(2)}
          tone={pfTone}
        />
        <Row
          icon={<Repeat size={16} />}
          label="Streaks W / L"
          value={`${data.max_consecutive_wins} / ${data.max_consecutive_losses}`}
        />
        <Row
          icon={<TrendingUp size={16} />}
          label="Best / Avg winner"
          value={`${formatUSDC(data.best_trade)} / ${formatUSDC(data.avg_winner)}`}
          tone="positive"
        />
        <Row
          icon={<TrendingDown size={16} />}
          label="Worst / Avg loser"
          value={`${formatUSDC(data.worst_trade)} / ${formatUSDC(data.avg_loser)}`}
          tone="negative"
        />
        <Row
          icon={<Clock size={16} />}
          label="Avg duration"
          value={`${data.avg_duration_ticks.toFixed(0)} ticks`}
        />
      </div>

      {data.by_exit_reason.length > 0 && (
        <div className="qm-subsection">
          <div className="qm-subheader">Exit reasons</div>
          <table className="data-table compact">
            <thead>
              <tr>
                <th>Reason</th>
                <th className="right">N</th>
                <th className="right">% trades</th>
                <th className="right">Avg PnL</th>
                <th className="right">Win rate</th>
              </tr>
            </thead>
            <tbody>
              {data.by_exit_reason.map((e) => (
                <tr key={e.reason}>
                  <td><strong>{e.reason}</strong></td>
                  <td className="right">{e.count}</td>
                  <td className="right text-muted">{e.pct_of_trades.toFixed(1)}%</td>
                  <td className={`right ${e.avg_pnl > 0 ? 'positive' : e.avg_pnl < 0 ? 'negative' : ''}`}>
                    {formatUSDC(e.avg_pnl)}
                  </td>
                  <td className="right">{formatPct(e.win_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.by_regime.length > 0 && (
        <div className="qm-subsection">
          <div className="qm-subheader">By regime</div>
          <table className="data-table compact">
            <thead>
              <tr>
                <th>Regime</th>
                <th className="right">N</th>
                <th className="right">Total PnL</th>
                <th className="right">Win rate</th>
              </tr>
            </thead>
            <tbody>
              {data.by_regime.map((r) => (
                <tr key={r.regime}>
                  <td><strong>{r.regime}</strong></td>
                  <td className="right">{r.count}</td>
                  <td className={`right ${r.total_pnl > 0 ? 'positive' : r.total_pnl < 0 ? 'negative' : ''}`}>
                    {formatUSDC(r.total_pnl)}
                  </td>
                  <td className="right">{formatPct(r.win_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
