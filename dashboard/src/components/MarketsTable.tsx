import React from 'react';
import { usePolling, formatVolume } from '../hooks/useApi';
import type { MarketOverview } from '../types';
import { Spinner } from './Common';

export const MarketsTable: React.FC = () => {
  const { data, loading } = usePolling<MarketOverview[]>('/dashboard/markets', 10_000);

  if (loading && !data) return <div className="table-card"><Spinner /></div>;
  if (!data) return <div className="table-card"><span style={{ color: 'var(--text-muted)' }}>Error loading markets</span></div>;

  return (
    <div className="table-card">
      <div className="section-header">
        <h2>🌐 Active Markets</h2>
        <span className="count-badge">{data.length}</span>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Asset</th>
              <th>Window</th>
              <th className="right">YES Price</th>
              <th className="right">Spread</th>
              <th className="right">Vol 24h</th>
              <th>WS</th>
              <th className="right">Ticks</th>
            </tr>
          </thead>
          <tbody>
            {!data.length ? (
              <tr><td colSpan={7} className="empty-row">No active markets</td></tr>
            ) : (
              data.map((m) => (
                <tr key={m.market_id}>
                  <td><strong>{m.asset}</strong></td>
                  <td className="text-muted">{m.window}</td>
                  <td className={`right ${m.yes_price >= 0.75 ? 'positive' : ''}`}>
                    {m.yes_price.toFixed(4)}
                  </td>
                  <td className="right text-muted">{m.spread.toFixed(4)}</td>
                  <td className="right text-muted">{formatVolume(m.volume_24h)}</td>
                  <td>
                    <span className={`ws-dot ${m.ws_connected ? 'connected' : 'disconnected'}`} />
                    {m.ws_connected ? 'ON' : 'OFF'}
                  </td>
                  <td className={`right ${m.consecutive_ticks >= 3 ? 'positive' : m.consecutive_ticks >= 1 ? 'neutral' : ''}`}>
                    {m.consecutive_ticks}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
