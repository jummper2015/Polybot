import React from 'react';
import { useApi, formatUSDC, formatDate } from '../hooks/useApi';
import type { OrdersListResponse } from '../types';
import type { StatusBadgeProps } from '../components/Common';
import { StatusBadge, Spinner } from '../components/Common';

type BadgeVariant = StatusBadgeProps['variant'];

const orderStatusVariant = (status: string): BadgeVariant => {
  switch (status.toLowerCase()) {
    case 'filled': return 'ok';
    case 'pending': return 'degraded';
    case 'confirmed': return 'running';
    case 'submitted': return 'connected';
    case 'failed': return 'down';
    case 'cancelled': return 'stopped';
    default: return 'stopped';
  }
};

export const Orders: React.FC = () => {
  const { data, loading, refetch } = useApi<OrdersListResponse>('/orders');

  if (loading && !data) return <div className="page"><div className="table-card"><Spinner /></div></div>;
  if (!data) return (
    <div className="page">
      <div className="table-card">
        <span className="text-muted">Error loading orders</span>
        <button className="btn-filter" onClick={refetch} style={{ marginLeft: 12 }}>Retry</button>
      </div>
    </div>
  );

  const orders = data.orders || [];
  const filled = orders.filter(o => o.status === 'FILLED').length;
  const failed = orders.filter(o => o.status === 'FAILED').length;

  return (
    <div className="page">
      <header className="dashboard-header">
        <div className="header-left">
          <h1>📋 Orders</h1>
          <StatusBadge label={`${orders.length} total`} variant="ok" size="sm" />
        </div>
        <div className="header-right">
          <span className="text-muted">
            {filled} filled · {failed} failed
          </span>
          <button className="btn-filter" onClick={refetch}>🔄 Refresh</button>
        </div>
      </header>

      <section className="section">
        <div className="table-card">
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Asset</th>
                  <th>Window</th>
                  <th>Side</th>
                  <th className="right">Amount</th>
                  <th className="right">Price</th>
                  <th>Status</th>
                  <th>Mode</th>
                  <th>Idemp. Key</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {!orders.length ? (
                  <tr><td colSpan={10} className="empty-row">No orders</td></tr>
                ) : (
                  orders.map((o) => (
                    <tr key={o.id}>
                      <td className="text-muted mono" style={{ fontSize: 11 }} title={o.id}>
                        {o.id.slice(0, 12)}...
                      </td>
                      <td><strong>{o.asset}</strong></td>
                      <td className="text-muted">{o.window}</td>
                      <td>
                        <StatusBadge label={o.side} variant={o.side === 'BUY' ? 'buy' : 'sell'} size="sm" />
                      </td>
                      <td className="right">{formatUSDC(o.amount)}</td>
                      <td className="right">{o.price.toFixed(4)}</td>
                      <td>
                        <StatusBadge label={o.status} variant={orderStatusVariant(o.status)} size="sm" />
                      </td>
                      <td>
                        <StatusBadge label={o.mode.toUpperCase()} variant={o.mode as 'paper' | 'real'} size="sm" />
                      </td>
                      <td className="text-muted mono" style={{ fontSize: 10 }}>
                        {o.idempotency_key ? o.idempotency_key.slice(0, 8) + '...' : '—'}
                      </td>
                      <td className="text-muted" style={{ fontSize: 11 }}>{formatDate(o.created_at)}</td>
                    </tr>
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
