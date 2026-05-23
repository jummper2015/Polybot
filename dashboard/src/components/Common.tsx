import React from 'react';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';

interface KpiCardProps {
  label: string;
  value: string;
  sub?: string;
  trend?: 'up' | 'down' | 'neutral';
  valueClassName?: string;
}

export const KpiCard: React.FC<KpiCardProps> = ({ label, value, sub, trend, valueClassName }) => {
  const trendIcon = trend === 'up' ? <TrendingUp size={14} /> :
                    trend === 'down' ? <TrendingDown size={14} /> :
                    null;

  const trendColor = trend === 'up' ? 'var(--green)' :
                     trend === 'down' ? 'var(--red)' :
                     'var(--text-muted)';

  return (
    <div className="kpi-card">
      <div className="kpi-label">
        {trendIcon && <span style={{ color: trendColor, marginRight: 4, verticalAlign: 'middle', display: 'inline-flex' }}>{trendIcon}</span>}
        {label}
      </div>
      <div className={`kpi-value ${valueClassName || ''}`}>{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
};

export interface StatusBadgeProps {
  label: string;
  variant: 'paper' | 'real' | 'running' | 'stopped' | 'ok' | 'degraded' | 'down' | 'connected' | 'disconnected' | 'open' | 'closed' | 'buy' | 'sell';
  size?: 'sm' | 'md';
}

const variantClass: Record<StatusBadgeProps['variant'], string> = {
  paper: 'badge badge-paper',
  real: 'badge badge-real',
  running: 'badge badge-running',
  stopped: 'badge badge-stopped',
  ok: 'badge badge-ok',
  degraded: 'badge badge-degraded',
  down: 'badge badge-down',
  connected: 'badge badge-ok',
  disconnected: 'badge badge-down',
  open: 'badge badge-running',
  closed: 'badge badge-stopped',
  buy: 'badge badge-buy',
  sell: 'badge badge-sell',
};

export const StatusBadge: React.FC<StatusBadgeProps> = ({ label, variant, size = 'md' }) => {
  return (
    <span className={`${variantClass[variant]} ${size === 'sm' ? 'badge-sm' : ''}`}>
      {label}
    </span>
  );
};

export const Spinner: React.FC<{ size?: number }> = ({ size = 20 }) => (
  <svg className="spinner" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="10" strokeOpacity="0.2" />
    <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
  </svg>
);
