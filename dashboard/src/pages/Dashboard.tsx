import React, { useState } from 'react';
import { SystemHealth } from '../components/Health';
import { KpiRow } from '../components/Health';
import { EquityChart } from '../components/EquityChart';
import { MarketsTable } from '../components/MarketsTable';
import { TradesTable } from '../components/TradesTable';
import { RegimePanel } from '../components/RegimePanel';
import { Spinner } from '../components/Common';
import { usePolling } from '../hooks/useApi';
import type { DashboardSummary } from '../types';

type EquityMode = 'all' | 'paper' | 'real';

export const Dashboard: React.FC = () => {
  const [equityMode, setEquityMode] = useState<EquityMode>('all');

  // Single source of truth for dashboard summary — lifted up to avoid double-fetching
  const { data: summary, loading } = usePolling<DashboardSummary>('/dashboard/summary', 10_000);

  const modeParam = equityMode === 'all' ? null : equityMode;

  return (
    <div className="page">
      {/* Header */}
      <header className="dashboard-header">
        <div className="header-left">
          <h1>🤖 Polymarket Bot</h1>
          {summary && (
            <>
              <span className={`badge badge-${summary.trading_mode}`}>{summary.trading_mode.toUpperCase()}</span>
              <span className={`badge ${summary.bot_running ? 'badge-running' : 'badge-stopped'}`}>
                {summary.bot_running ? 'RUNNING' : 'STOPPED'}
              </span>
            </>
          )}
          {loading && <Spinner size={16} />}
        </div>
        <div className="header-right">
          <span className="text-muted">Updates every 10s</span>
          <span className="text-muted">{new Date().toLocaleTimeString()}</span>
        </div>
      </header>

      {/* System Health — receives summary from parent */}
      <SystemHealth summary={summary} loading={loading} />

      {/* KPI Row — receives summary from parent */}
      <KpiRow summary={summary} loading={loading} />

      {/* Equity Chart */}
      <section className="section">
        <div className="section-header">
          <h2>📊 Equity Curve</h2>
          <div className="btn-group">
            <button
              className={`btn-filter ${equityMode === 'all' ? 'active' : ''}`}
              onClick={() => setEquityMode('all')}
            >
              All
            </button>
            <button
              className={`btn-filter ${equityMode === 'paper' ? 'active' : ''}`}
              onClick={() => setEquityMode('paper')}
            >
              Paper
            </button>
            <button
              className={`btn-filter ${equityMode === 'real' ? 'active' : ''}`}
              onClick={() => setEquityMode('real')}
            >
              Real
            </button>
          </div>
        </div>
        <EquityChart mode={modeParam} />
      </section>

      {/* Regime Panel (P11.1) */}
      <section className="section">
        <RegimePanel />
      </section>

      {/* Tables */}
      <section className="bottom-grid">
        <MarketsTable />
        <TradesTable />
      </section>
    </div>
  );
};
