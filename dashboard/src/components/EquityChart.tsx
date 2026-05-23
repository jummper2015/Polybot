import React from 'react';
import { usePolling, formatUSDC } from '../hooks/useApi';
import type { EquityPoint } from '../types';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import { Spinner } from './Common';

interface Props {
  mode?: 'paper' | 'real' | null;
}

const CustomTooltip: React.FC<{ active?: boolean; payload?: unknown[]; label?: string }> = ({ active, payload, label }) => {
  if (!active || !payload || !payload.length) return null;
  const data = payload[0] as { payload: EquityPoint & { formattedTime: string } };
  return (
    <div className="chart-tooltip">
      <div className="tooltip-time">{data.payload.formattedTime}</div>
      <div className="tooltip-pnl" style={{ color: data.payload.cumulative_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
        PnL: {formatUSDC(data.payload.cumulative_pnl)}
      </div>
      {data.payload.exit_reason && (
        <div className="tooltip-reason">Razón: {data.payload.exit_reason.split(':')[0]}</div>
      )}
    </div>
  );
};

export const EquityChart: React.FC<Props> = ({ mode }) => {
  const params = mode ? `?mode=${mode}&limit=200` : '?limit=200';
  const { data, loading } = usePolling<EquityPoint[]>(`/dashboard/equity${params}`, 15_000);

  if (loading && !data) {
    return <div className="chart-container" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spinner /></div>;
  }
  if (!data || !data.length) {
    return <div className="chart-container" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <span style={{ color: 'var(--text-muted)' }}>Sin datos de equity aún</span>
    </div>;
  }

  const chartData = data.map((pt) => ({
    ...pt,
    formattedTime: new Date(pt.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  }));

  const lastPnl = chartData[chartData.length - 1].cumulative_pnl;
  const lineColor = lastPnl >= 0 ? '#22c55e' : '#ef4444';
  const fillColor = lastPnl >= 0 ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';

  const minPnl = Math.min(0, ...chartData.map(d => d.cumulative_pnl));
  const maxPnl = Math.max(0, ...chartData.map(d => d.cumulative_pnl));
  const padding = (maxPnl - minPnl) * 0.1 || 10;

  return (
    <div className="chart-container">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 5, right: 15, left: 0, bottom: 5 }}>
          <defs>
            <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity={0.3} />
              <stop offset="100%" stopColor={lineColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="formattedTime"
            stroke="var(--text-muted)"
            tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
            tickLine={false}
            axisLine={{ stroke: 'var(--border)' }}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke="var(--text-muted)"
            tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
            tickLine={false}
            axisLine={{ stroke: 'var(--border)' }}
            tickFormatter={(v: number) => `$${v.toFixed(0)}`}
            domain={[minPnl - padding, maxPnl + padding]}
          />
          <Tooltip content={<CustomTooltip />} />
          <Area
            type="monotone"
            dataKey="cumulative_pnl"
            stroke={lineColor}
            strokeWidth={2}
            fill="url(#equityGradient)"
            dot={false}
            activeDot={{ r: 4, fill: lineColor, stroke: 'var(--bg)' }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};
