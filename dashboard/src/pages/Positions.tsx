import React from 'react';
import { useApi, formatUSDC, formatPct, formatDate } from '../hooks/useApi';
import type { PositionsListResponse, Position } from '../types';
import { StatusBadge, Spinner } from '../components/Common';

export const Positions: React.FC = () => {
  const { data, loading, refetch } = useApi<PositionsListResponse>('/positions');

  if (loading && !data) return <div className="page"><div className="table-card"><Spinner /></div></div>;
  if (!data) return (
    <div className="page">
      <div className="table-card">
        <span className="text-muted">Error loading positions</span>
        <button className="btn-filter" onClick={refetch} style={{ marginLeft: 12 }}>Retry</button>
      </div>
    </div>
  );

  const positions = data.positions || [];

  const openPositions = positions.filter((p) => p.is_open);
  const closedPositions = positions.filter((p) => !p.is_open);
  const totalPnl = closedPositions.reduce((sum, p) => sum + (p.pnl || 0), 0);

  return (
    <div className="page">
      <header className="dashboard-header">
        <div className="header-left">
          <h1>💼 Positions</h1>
          <StatusBadge label={`${openPositions.length} open`} variant="ok" size="sm" />
        </div>
        <div className="header-right">
          <span className={`kpi-sub ${totalPnl >= 0 ? 'positive' : 'negative'}`}>
            Total PnL: {formatUSDC(totalPnl)}
          </span>
          <button className="btn-filter" onClick={refetch}>🔄 Refresh</button>
        </div>
      </header>

      {openPositions.length > 0 && (
        <section className="section">
          <div className="section-header">
            <h2>🟢 Open ({openPositions.length})</h2>
          </div>
          <div className="table-card">
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Window</th>
                    <th>Side</th>
                    <th className="right">Amount</th>
                    <th className="right">Entry</th>
                    <th className="right">Current</th>
                    <th className="right">Unreal. PnL</th>
                    <th>Opened</th>
                  </tr>
                </thead>
                <tbody>
                  {openPositions.map((p) => (
                    <PositionRow key={p.id} pos={p} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      <section className="section">
        <div className="section-header">
          <h2>📋 Closed ({closedPositions.length})</h2>
        </div>
        <div className="table-card">
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Window</th>
                  <th>Side</th>
                  <th className="right">Amount</th>
                  <th className="right">Entry</th>
                  <th className="right">Exit</th>
                  <th className="right">PnL</th>
                  <th>Reason</th>
                  <th>Closed</th>
                </tr>
              </thead>
              <tbody>
                {!closedPositions.length ? (
                  <tr><td colSpan={9} className="empty-row">No closed positions</td></tr>
                ) : (
                  closedPositions.map((p) => (
                    <ClosedPositionRow key={p.id} pos={p} />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
};

const PositionRow: React.FC<{ pos: Position }> = ({ pos }) => {
  const unrealPnl = pos.pnl ?? (pos.current_price ? (pos.current_price - pos.entry_price) * pos.amount : null);
  const pnlClass = unrealPnl != null ? (unrealPnl >= 0 ? 'positive' : 'negative') : '';

  return (
    <tr>
      <td><strong>{pos.asset}</strong></td>
      <td className="text-muted">{pos.window}</td>
      <td><StatusBadge label={pos.side} variant={pos.side === 'BUY' ? 'buy' : 'sell'} size="sm" /></td>
      <td className="right">{formatUSDC(pos.amount)}</td>
      <td className="right">{pos.entry_price.toFixed(4)}</td>
      <td className="right">{pos.current_price != null ? pos.current_price.toFixed(4) : '—'}</td>
      <td className={`right ${pnlClass}`}>
        {unrealPnl != null ? (
          <><span>{formatUSDC(unrealPnl)}</span><br /><small>{pos.pnl_pct != null ? formatPct(pos.pnl_pct) : '—'}</small></>
        ) : '—'}
      </td>
      <td className="text-muted" style={{ fontSize: 11 }}>{formatDate(pos.opened_at)}</td>
    </tr>
  );
};

const ClosedPositionRow: React.FC<{ pos: Position }> = ({ pos }) => {
  const pnlClass = pos.pnl != null ? (pos.pnl >= 0 ? 'positive' : 'negative') : '';

  return (
    <tr>
      <td><strong>{pos.asset}</strong></td>
      <td className="text-muted">{pos.window}</td>
      <td><StatusBadge label={pos.side} variant={pos.side === 'BUY' ? 'buy' : 'sell'} size="sm" /></td>
      <td className="right">{formatUSDC(pos.amount)}</td>
      <td className="right">{pos.entry_price.toFixed(4)}</td>
      <td className="right">{pos.exit_price != null ? pos.exit_price.toFixed(4) : '—'}</td>
      <td className={`right ${pnlClass}`}>
        {pos.pnl != null ? (
          <><span>{formatUSDC(pos.pnl)}</span><br /><small>{formatPct(pos.pnl_pct)}</small></>
        ) : '—'}
      </td>
      <td className="text-muted" style={{ fontSize: 11 }}>{pos.exit_reason || '—'}</td>
      <td className="text-muted" style={{ fontSize: 11 }}>{formatDate(pos.closed_at)}</td>
    </tr>
  );
};
