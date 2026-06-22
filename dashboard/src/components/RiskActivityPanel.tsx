import React from 'react';
import { ShieldAlert, ShieldCheck, ShieldX } from 'lucide-react';
import { usePolling, formatPct } from '../hooks/useApi';
import type { RiskActivity } from '../types';
import { Spinner } from './Common';

const Bar: React.FC<{ value: number; max: number; label: string; warningAt?: number; criticalAt?: number }> = ({
  value, max, label, warningAt = 0.7, criticalAt = 0.9,
}) => {
  const fillPct = Math.min(100, (value / max) * 100);
  const tone = value / max >= criticalAt ? '#ef4444'
             : value / max >= warningAt ? '#eab308'
             : '#22c55e';
  return (
    <div className="risk-bar">
      <div className="risk-bar-header">
        <span>{label}</span>
        <strong style={{ color: tone }}>{formatPct(value)}</strong>
      </div>
      <div className="risk-bar-bg">
        <div className="risk-bar-fill" style={{ width: `${fillPct}%`, backgroundColor: tone }} />
        <div className="risk-bar-limit" style={{ left: `${(criticalAt * 100)}%` }} />
      </div>
      <div className="risk-bar-sub text-muted">limit: {formatPct(max)}</div>
    </div>
  );
};

export const RiskActivityPanel: React.FC = () => {
  const { data, loading } = usePolling<RiskActivity>('/dashboard/risk-activity', 10_000);

  if (loading && !data) {
    return (
      <div className="table-card">
        <div className="section-header"><h2>🛡️ Risk Engine</h2></div>
        <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}><Spinner /></div>
      </div>
    );
  }
  if (!data) return null;

  const total = data.allow_count + data.deny_count;
  const denyPct = total > 0 ? data.deny_count / total : 0;

  // Drawdown / exposure limits desde la config del bot (estos son los valores estándar
  // del .env.example: max_drawdown 10%, max_exposure 30%).
  const MAX_DRAWDOWN = 0.10;
  const MAX_EXPOSURE = 0.30;

  return (
    <div className="table-card">
      <div className="section-header">
        <h2>
          <ShieldCheck size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
          Risk Engine
        </h2>
        <span className={`badge badge-sm ${data.mode === 'real' ? 'badge-real' : 'badge-paper'}`}>
          {data.mode.toUpperCase()}
        </span>
      </div>

      <div className="risk-summary">
        <div className="risk-stat">
          <ShieldCheck size={14} style={{ color: '#22c55e' }} />
          <span>Allowed</span>
          <strong className="positive">{data.allow_count.toLocaleString()}</strong>
        </div>
        <div className="risk-stat">
          <ShieldX size={14} style={{ color: '#ef4444' }} />
          <span>Denied</span>
          <strong className="negative">{data.deny_count.toLocaleString()}</strong>
        </div>
        <div className="risk-stat">
          <ShieldAlert size={14} style={{ color: denyPct > 0.3 ? '#ef4444' : '#8892a4' }} />
          <span>Deny rate</span>
          <strong>{(denyPct * 100).toFixed(1)}%</strong>
        </div>
      </div>

      <div className="risk-gauges">
        <Bar value={data.drawdown_pct} max={MAX_DRAWDOWN} label="Drawdown" warningAt={0.5} criticalAt={0.8} />
        <Bar value={data.exposure_pct} max={MAX_EXPOSURE} label="Exposure" warningAt={0.7} criticalAt={0.9} />
      </div>

      <div className="qm-subsection">
        <div className="qm-subheader">6 rules — denies acumulados desde arranque</div>
        <table className="data-table compact">
          <thead>
            <tr>
              <th>Rule</th>
              <th className="right">Priority</th>
              <th className="right">Denies</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {data.rules.map((r) => (
              <tr key={r.rule}>
                <td><strong>{r.rule.replace(/Rule$/, '')}</strong></td>
                <td className="right text-muted">P{r.priority}</td>
                <td className={`right ${r.deny_count > 0 ? 'negative' : ''}`}>{r.deny_count}</td>
                <td>
                  {r.deny_count === 0 ? (
                    <span className="badge badge-sm badge-ok">CLEAR</span>
                  ) : (
                    <span className="badge badge-sm badge-down">BLOCKING</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
