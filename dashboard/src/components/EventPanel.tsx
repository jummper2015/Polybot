import React from 'react';
import { Zap, Octagon, AlertTriangle } from 'lucide-react';
import { usePolling } from '../hooks/useApi';
import type { EventActivity } from '../types';
import { Spinner } from './Common';

const EVENT_TYPE_COLOR: Record<string, string> = {
  PRICE_SHOCK:       '#ef4444',
  VOLUME_SURGE:      '#f59e0b',
  EXPIRY_PROXIMITY:  '#8b5cf6',
  SPREAD_EXPLOSION:  '#06b6d4',
};

const SEVERITY_COLOR: Record<string, string> = {
  LOW:      '#22c55e',
  MEDIUM:   '#eab308',
  HIGH:     '#f97316',
  CRITICAL: '#ef4444',
};

const ACTION_COLOR: Record<string, string> = {
  ALLOW:            '#22c55e',
  BOOST_CONFIDENCE: '#06b6d4',
  REDUCE_SIZE:      '#eab308',
  HALT:             '#ef4444',
};

export const EventPanel: React.FC = () => {
  const { data, loading } = usePolling<EventActivity>('/dashboard/events', 10_000);

  if (loading && !data) {
    return (
      <div className="table-card">
        <div className="section-header"><h2>⚡ Event Detector (P11.4)</h2></div>
        <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}><Spinner /></div>
      </div>
    );
  }
  if (!data) return null;

  const totalDetections = data.by_type.reduce((sum, e) => sum + e.count, 0);
  const hasActivity = totalDetections > 0 || data.active_halts.length > 0;

  return (
    <div className="table-card">
      <div className="section-header">
        <h2>
          <Zap size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
          Event Detector (P11.4)
        </h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {data.active_halts.length > 0 && (
            <span className="badge badge-sm badge-down" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <Octagon size={12} /> {data.active_halts.length} HALT
            </span>
          )}
          <span className="count-badge">{totalDetections} detected</span>
        </div>
      </div>

      {!hasActivity && (
        <div className="empty-row" style={{ padding: 16, textAlign: 'center' }}>
          <span className="text-muted">
            Sin eventos detectados desde el arranque. Los contadores aparecen aquí cuando el orchestrator detecte price shocks, volume surges, expiraciones o spreads anómalos.
          </span>
        </div>
      )}

      {data.by_type.length > 0 && (
        <div className="qm-subsection">
          <div className="qm-subheader">By type × severity</div>
          <table className="data-table compact">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Type</th>
                <th>Severity</th>
                <th className="right">Count</th>
              </tr>
            </thead>
            <tbody>
              {data.by_type.map((e, i) => (
                <tr key={`${e.asset}-${e.event_type}-${e.severity}-${i}`}>
                  <td><strong>{e.asset}</strong></td>
                  <td>
                    <span style={{
                      color: EVENT_TYPE_COLOR[e.event_type] || '#8892a4',
                      fontWeight: 600,
                      fontSize: 12,
                    }}>
                      {e.event_type.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td>
                    <span className="badge badge-sm" style={{
                      backgroundColor: (SEVERITY_COLOR[e.severity] || '#444') + '22',
                      color: SEVERITY_COLOR[e.severity] || '#8892a4',
                      border: `1px solid ${SEVERITY_COLOR[e.severity] || '#444'}`,
                    }}>
                      {e.severity}
                    </span>
                  </td>
                  <td className="right"><strong>{e.count}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.by_action.length > 0 && (
        <div className="qm-subsection">
          <div className="qm-subheader">Responses (action taken by orchestrator)</div>
          <table className="data-table compact">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Action</th>
                <th className="right">Count</th>
              </tr>
            </thead>
            <tbody>
              {data.by_action.map((a, i) => (
                <tr key={`${a.asset}-${a.action}-${i}`}>
                  <td><strong>{a.asset}</strong></td>
                  <td>
                    <span style={{
                      color: ACTION_COLOR[a.action] || '#8892a4',
                      fontWeight: 600,
                      fontSize: 12,
                    }}>
                      {a.action.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="right"><strong>{a.count}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.halt_entries > 0 && (
        <div className="qm-subsection" style={{
          marginTop: 12, padding: 10, borderRadius: 6,
          background: '#3f1f1f', border: '1px solid #ef4444',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <AlertTriangle size={16} style={{ color: '#ef4444' }} />
          <span style={{ color: '#ef4444', fontWeight: 600 }}>{data.halt_entries}</span>
          <span className="text-muted">entries bloqueadas por HALT desde arranque</span>
        </div>
      )}

      {data.active_halts.length > 0 && (
        <div className="qm-subsection">
          <div className="qm-subheader" style={{ color: '#ef4444' }}>Active HALTs</div>
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {data.active_halts.map((h, i) => (
              <li key={i} style={{
                padding: '6px 8px', marginBottom: 4, borderRadius: 4,
                background: '#3f1f1f', border: '1px solid #ef4444',
                fontFamily: 'monospace', fontSize: 12,
              }}>
                <strong style={{ color: '#ef4444' }}>{h.asset}</strong>
                {' · '}
                <span className="text-muted">{h.market_id.slice(0, 16)}…</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};
