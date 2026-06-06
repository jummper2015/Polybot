import React from 'react';
import { usePolling } from '../hooks/useApi';
import type { RegimeInfo, RegimeType } from '../types';
import { Spinner } from './Common';

// ── Regime color mapping ────────────────────────────────────────────────
const REGIME_COLORS: Record<RegimeType, { color: string; bg: string; emoji: string; label: string }> = {
  trend:        { color: '#22c55e', bg: '#1a3d2b', emoji: '📈', label: 'TREND' },
  chop:         { color: '#8892a4', bg: '#2d2d2d', emoji: '🔄', label: 'CHOP' },
  panic:        { color: '#ef4444', bg: '#3f1f1f', emoji: '🚨', label: 'PANIC' },
  illiquid:     { color: '#f97316', bg: '#3d2a1a', emoji: '💧', label: 'ILLIQUID' },
  event_driven: { color: '#8b5cf6', bg: '#2a1f3d', emoji: '📅', label: 'EVENT' },
};

const RegimeBadge: React.FC<{ regime: RegimeType }> = ({ regime }) => {
  const cfg = REGIME_COLORS[regime] ?? REGIME_COLORS.chop;
  return (
    <span
      className="regime-badge"
      style={{ backgroundColor: cfg.bg, color: cfg.color, borderColor: cfg.color }}
    >
      {cfg.emoji} {cfg.label}
    </span>
  );
};

const RegimeCard: React.FC<{ market: RegimeInfo }> = ({ market }) => {
  const confidencePct = Math.round(market.confidence * 100);

  return (
    <div className="regime-card">
      <div className="regime-card-header">
        <span className="regime-market-label">
          {market.asset}/{market.window}
        </span>
        <RegimeBadge regime={market.regime} />
      </div>
      <div className="regime-card-body">
        <div className="regime-confidence">
          <div className="confidence-bar-bg">
            <div
              className="confidence-bar-fill"
              style={{
                width: `${confidencePct}%`,
                backgroundColor: market.confidence > 0.7 ? '#22c55e' : market.confidence > 0.4 ? '#eab308' : '#ef4444',
              }}
            />
          </div>
          <span className="confidence-text">{confidencePct}% confidence</span>
        </div>
        <div className="regime-strategies">
          {market.strategies_active.length > 0 && (
            <div className="strategy-group">
              <span className="strategy-label active-label">Active</span>
              {market.strategies_active.map((s) => (
                <span key={s} className="strategy-tag strategy-active">{s}</span>
              ))}
            </div>
          )}
          {market.strategies_inactive.length > 0 && (
            <div className="strategy-group">
              <span className="strategy-label inactive-label">Inactive</span>
              {market.strategies_inactive.map((s) => (
                <span key={s} className="strategy-tag strategy-inactive">{s}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export const RegimePanel: React.FC = () => {
  const { data, loading } = usePolling<RegimeInfo[]>('/dashboard/regimes', 10_000);

  if (loading && !data) {
    return (
      <div className="table-card">
        <div className="section-header">
          <h2>🌡️ Market Regimes</h2>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40 }}>
          <Spinner />
        </div>
      </div>
    );
  }

  if (!data || !data.length) {
    return (
      <div className="table-card">
        <div className="section-header">
          <h2>🌡️ Market Regimes</h2>
          <span className="update-badge">Orchestrator not active</span>
        </div>
        <div className="empty-row" style={{ display: 'block', textAlign: 'center', padding: 24 }}>
          <span className="text-muted">
            {!data ? 'Waiting for regime data...' : 'No active markets — start paper trading to see regimes'}
          </span>
        </div>
      </div>
    );
  }

  const enabled = data[0]?.orchestrator_enabled;

  return (
    <div className="table-card">
      <div className="section-header">
        <h2>🌡️ Market Regimes</h2>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span className={`badge badge-sm ${enabled ? 'badge-running' : 'badge-stopped'}`}>
            {enabled ? 'ENABLED' : 'DISABLED'}
          </span>
          <span className="count-badge">{data.length}</span>
        </div>
      </div>
      <div className="regime-grid">
        {data.map((m) => (
          <RegimeCard key={`${m.asset}-${m.window}`} market={m} />
        ))}
      </div>
    </div>
  );
};
