import React, { useState } from 'react';
import { usePolling, formatVolume } from '../hooks/useApi';
import type { MarketOverview, OrderbookLevel } from '../types';
import { Spinner } from './Common';

const OrderbookPreview: React.FC<{ bids: OrderbookLevel[]; asks: OrderbookLevel[] }> = ({ bids, asks }) => {
  const maxRows = 5;
  const topBids = bids.slice(0, maxRows);
  const topAsks = asks.slice(0, maxRows);

  if (!topBids.length && !topAsks.length) {
    return <div className="ob-empty">Waiting for orderbook data...</div>;
  }

  return (
    <div className="ob-grid">
      <div className="ob-side ob-bids">
        <div className="ob-header">Bids</div>
        {topBids.map((b, i) => (
          <div key={i} className="ob-row">
            <span className="ob-price bid-price">{b.price.toFixed(4)}</span>
            <span className="ob-size">{b.size.toLocaleString()}</span>
          </div>
        ))}
        {bids.length > maxRows && (
          <div className="ob-more">+{bids.length - maxRows} more levels</div>
        )}
      </div>
      <div className="ob-side ob-asks">
        <div className="ob-header">Asks</div>
        {topAsks.map((a, i) => (
          <div key={i} className="ob-row">
            <span className="ob-price ask-price">{a.price.toFixed(4)}</span>
            <span className="ob-size">{a.size.toLocaleString()}</span>
          </div>
        ))}
        {asks.length > maxRows && (
          <div className="ob-more">+{asks.length - maxRows} more levels</div>
        )}
      </div>
    </div>
  );
};

export const MarketsTable: React.FC = () => {
  const { data, loading } = usePolling<MarketOverview[]>('/dashboard/markets', 10_000);
  const [expandedMarket, setExpandedMarket] = useState<string | null>(null);

  if (loading && !data) return <div className="table-card"><Spinner /></div>;
  if (!data) return <div className="table-card"><span style={{ color: 'var(--text-muted)' }}>Error loading markets</span></div>;

  const toggleOrderbook = (marketId: string) => {
    setExpandedMarket(expandedMarket === marketId ? null : marketId);
  };

  return (
    <div className="table-card">
      <div className="section-header">
        <h2>🌐 Live Markets</h2>
        <span className="count-badge">{data.length}</span>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Asset</th>
              <th>Window</th>
              <th className="right">Bid</th>
              <th className="right">Mid</th>
              <th className="right">Ask</th>
              <th className="right">Spread</th>
              <th className="right">Vol 24h</th>
              <th>WS</th>
              <th>OB</th>
            </tr>
          </thead>
          <tbody>
            {!data.length ? (
              <tr><td colSpan={9} className="empty-row">No active markets</td></tr>
            ) : (
              data.map((m) => {
                const isExpanded = expandedMarket === m.market_id;
                const hasOrderbook = m.orderbook_bids.length > 0 || m.orderbook_asks.length > 0;

                return (
                  <React.Fragment key={m.market_id}>
                    <tr
                      className={isExpanded ? 'row-expanded' : ''}
                      onClick={() => toggleOrderbook(m.market_id)}
                      style={{ cursor: 'pointer' }}
                    >
                      <td><strong>{m.asset}</strong></td>
                      <td className="text-muted">{m.window}</td>
                      <td className="right bid-color">{m.best_bid.toFixed(4)}</td>
                      <td className={`right ${m.yes_price >= 0.75 ? 'positive' : ''}`}>
                        {m.yes_price.toFixed(4)}
                      </td>
                      <td className="right ask-color">{m.best_ask.toFixed(4)}</td>
                      <td className="right text-muted">{m.spread.toFixed(4)}</td>
                      <td className="right text-muted">{formatVolume(m.volume_24h)}</td>
                      <td>
                        <span className={`ws-dot ${m.ws_connected ? 'connected' : 'disconnected'}`} />
                        {m.ws_connected ? 'ON' : 'OFF'}
                      </td>
                      <td>
                        <span className={`ob-indicator ${hasOrderbook ? 'has-data' : ''}`}>
                          {hasOrderbook ? '📖' : '—'}
                        </span>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="orderbook-row">
                        <td colSpan={9}>
                          <OrderbookPreview bids={m.orderbook_bids} asks={m.orderbook_asks} />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
