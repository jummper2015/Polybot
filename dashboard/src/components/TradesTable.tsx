import React, { useState } from 'react';
import { usePolling, formatUSDC, formatPct } from '../hooks/useApi';
import type { RecentTrade } from '../types';
import { StatusBadge, Spinner } from './Common';

export const TradesTable: React.FC = () => {
  const [openOnly, setOpenOnly] = useState(false);
  const params = openOnly ? '?open_only=true&limit=50' : '?limit=50';
  const { data, loading } = usePolling<RecentTrade[]>(`/dashboard/trades${params}`, 10_000, [openOnly]);

  if (loading && !data) return <div className="table-card"><Spinner /></div>;

  return (
    <div className="table-card">
      <div className="section-header">
        <h2>💼 Recent Trades</h2>
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={openOnly}
            onChange={(e) => setOpenOnly(e.target.checked)}
          />
          <span>Only open</span>
        </label>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Asset</th>
              <th>Side</th>
              <th className="right">Amount</th>
              <th className="right">Entry</th>
              <th className="right">Exit</th>
              <th className="right">PnL</th>
              <th>Reason</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {!data || !data.length ? (
              <tr><td colSpan={8} className="empty-row">No trades</td></tr>
            ) : (
              data.map((t) => {
                const pnlClass = t.pnl == null ? '' : (t.pnl >= 0 ? 'positive' : 'negative');
                return (
                  <tr key={t.id}>
                    <td>
                      <strong>{t.asset}</strong>
                      <span className="text-muted" style={{ fontSize: 11, marginLeft: 4 }}>{t.window}</span>
                    </td>
                    <td>
                      <StatusBadge label={t.side} variant={t.side === 'BUY' ? 'buy' : 'sell'} size="sm" />
                    </td>
                    <td className="right">{formatUSDC(t.amount)}</td>
                    <td className="right">{t.entry_price.toFixed(4)}</td>
                    <td className="right">{t.exit_price != null ? t.exit_price.toFixed(4) : '—'}</td>
                    <td className={`right ${pnlClass}`}>
                      {t.pnl == null ? (
                        <span className="text-muted">Open</span>
                      ) : (
                        <><span>{formatUSDC(t.pnl)}</span><br /><small>{formatPct(t.pnl_pct)}</small></>
                      )}
                    </td>
                    <td className="text-muted" style={{ fontSize: 11 }}>
                      {t.exit_reason ? t.exit_reason.split(':')[0] : '—'}
                    </td>
                    <td>
                      <StatusBadge label={t.is_open ? 'OPEN' : 'CLOSED'} variant={t.is_open ? 'open' : 'closed'} size="sm" />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
