// src/interfaces/api/static/js/dashboard.js

// ── Estado global ─────────────────────────────────────────────────────
const state = {
  equityMode: null,       // null | 'paper' | 'real'
  openOnly:   false,
  chart:      null,
  polling:    null,
};

const API = '/api/v1';
const POLL_INTERVAL_MS = 10_000;   // 10 segundos

// ── Arranque ──────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initChart();
  fetchAll();
  state.polling = setInterval(fetchAll, POLL_INTERVAL_MS);
});

async function fetchAll() {
  await Promise.allSettled([
    fetchSummary(),
    fetchEquity(),
    fetchMarkets(),
    fetchTrades(),
  ]);
  document.getElementById('last-update').textContent =
    new Date().toLocaleTimeString();
}

// ── Summary / KPIs ────────────────────────────────────────────────────
async function fetchSummary() {
  const data = await apiFetch(`${API}/dashboard/summary`);
  if (!data) return;

  // Balance
  setText('kpi-balance', formatUSDC(data.balance));
  const balDiff = data.balance - data.initial_balance;
  setColoredText('kpi-balance-change',
    `${formatUSDC(balDiff)} desde inicio`, balDiff);

  // PnL
  setColoredText('kpi-pnl', formatUSDC(data.total_pnl_usdc), data.total_pnl_usdc);
  setColoredText('kpi-pnl-pct', formatPct(data.total_pnl_pct), data.total_pnl_pct);

  // Win Rate
  setText('kpi-winrate', formatPct(data.win_rate));
  setText('kpi-trades',
    `${data.closed_positions} trades cerrados`);

  // Profit Factor
  const pfEl = document.getElementById('kpi-pf');
  pfEl.textContent  = data.profit_factor.toFixed(2);
  pfEl.className    = 'kpi-value ' +
    (data.profit_factor >= 1.5 ? 'positive' :
     data.profit_factor >= 1.0 ? 'neutral'  : 'negative');

  // Max Drawdown
  const ddEl = document.getElementById('kpi-maxdd');
  ddEl.textContent = formatPct(data.max_drawdown_pct);
  ddEl.className   = 'kpi-value ' +
    (data.max_drawdown_pct < 0.05 ? 'positive' :
     data.max_drawdown_pct < 0.08 ? 'neutral'  : 'negative');
  setText('kpi-drawdown-now', `actual: ${formatPct(data.drawdown_pct)}`);

  // Mercados y posiciones
  setText('kpi-markets', data.active_markets);
  setText('kpi-positions', `${data.open_positions} posiciones abiertas`);

  // Mode badge
  const modeEl = document.getElementById('mode-badge');
  modeEl.textContent = data.trading_mode.toUpperCase();
  modeEl.className   = `badge badge-${data.trading_mode}`;

  // Bot status
  const statusEl = document.getElementById('bot-status');
  statusEl.textContent = data.bot_running ? 'CORRIENDO' : 'DETENIDO';
  statusEl.className   = `badge ${data.bot_running ? 'badge-running' : 'badge-stopped'}`;
}

// ── Equity Chart ──────────────────────────────────────────────────────
function initChart() {
  const ctx = document.getElementById('equityChart').getContext('2d');
  state.chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels:   [],
      datasets: [{
        label:           'PnL Acumulado (USDC)',
        data:            [],
        borderColor:     '#3b82f6',
        backgroundColor: 'rgba(59,130,246,0.08)',
        borderWidth:     2,
        pointRadius:     0,
        pointHoverRadius: 4,
        fill:            true,
        tension:         0.3,
      }],
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => `PnL: ${formatUSDC(ctx.parsed.y)}`,
            afterLabel: (ctx) => {
              const pt = ctx.raw;
              return pt.exit_reason ? `Razón: ${pt.exit_reason}` : '';
            },
          },
        },
      },
      scales: {
        x: {
          grid:   { color: '#2d3148' },
          ticks:  {
            color:    '#8892a4',
            maxRotation: 0,
            maxTicksLimit: 8,
            callback: (_, i, arr) => {
              const labels = state.chart.data.labels;
              if (!labels[i]) return '';
              const d = new Date(labels[i]);
              return d.toLocaleTimeString([], { hour:'2-digit', minute:'2-digit' });
            },
          },
        },
        y: {
          grid:  { color: '#2d3148' },
          ticks: {
            color:    '#8892a4',
            callback: (v) => `$${v.toFixed(2)}`,
          },
        },
      },
    },
  });
}

async function fetchEquity() {
  const params = state.equityMode ? `?mode=${state.equityMode}&limit=200` : '?limit=200';
  const data   = await apiFetch(`${API}/dashboard/equity${params}`);
  if (!data || !data.length) return;

  const labels = data.map(p => p.timestamp);
  const values = data.map(p => ({
    x:           p.timestamp,
    y:           p.cumulative_pnl,
    exit_reason: p.exit_reason,
  }));

  // Color dinámico según PnL final
  const lastPnl   = data[data.length - 1].cumulative_pnl;
  const lineColor = lastPnl >= 0 ? '#22c55e' : '#ef4444';
  const fillColor = lastPnl >= 0
    ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';

  state.chart.data.labels             = labels;
  state.chart.data.datasets[0].data   = values;
  state.chart.data.datasets[0].borderColor     = lineColor;
  state.chart.data.datasets[0].backgroundColor = fillColor;
  state.chart.update('none');  // Sin animación en updates de polling
}

function setEquityMode(mode, btn) {
  state.equityMode = mode;
  document.querySelectorAll('.btn-filter').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  fetchEquity();
}

// ── Markets Table ─────────────────────────────────────────────────────
async function fetchMarkets() {
  const data = await apiFetch(`${API}/dashboard/markets`);
  if (!data) return;

  const tbody = document.getElementById('markets-table-body');
  document.getElementById('markets-count').textContent = data.length;

  if (!data.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Sin mercados activos</td></tr>';
    return;
  }

  tbody.innerHTML = data.map(m => {
    const wsClass = m.ws_connected ? 'connected' : 'disconnected';
    const ticksColor = m.consecutive_ticks >= 3 ? 'positive' :
                       m.consecutive_ticks >= 1 ? 'neutral' : '';
    return `
      <tr>
        <td><strong>${m.asset}</strong></td>
        <td>${m.window}</td>
        <td class="${m.yes_price >= 0.75 ? 'positive' : ''}">
          ${m.yes_price.toFixed(4)}
        </td>
        <td>${m.spread.toFixed(4)}</td>
        <td>${formatVolume(m.volume_24h)}</td>
        <td>
          <span class="ws-dot ${wsClass}"></span>
          ${m.ws_connected ? 'ON' : 'OFF'}
        </td>
        <td class="${ticksColor}">${m.consecutive_ticks}</td>
      </tr>
    `;
  }).join('');
}

// ── Trades Table ──────────────────────────────────────────────────────
async function fetchTrades() {
  const params = state.openOnly ? '?open_only=true&limit=50' : '?limit=50';
  const data   = await apiFetch(`${API}/dashboard/trades${params}`);
  if (!data) return;

  const tbody = document.getElementById('trades-table-body');

  if (!data.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-row">Sin trades</td></tr>';
    return;
  }

  tbody.innerHTML = data.map(t => {
    const pnlClass = t.pnl == null ? '' : (t.pnl >= 0 ? 'positive' : 'negative');
    const pnlText  = t.pnl == null
      ? '<span class="neutral">Abierta</span>'
      : `<span class="${pnlClass}">${formatUSDC(t.pnl)}<br>
         <small>${formatPct(t.pnl_pct)}</small></span>`;
    const statusBadge = t.is_open
      ? '<span class="badge badge-running" style="font-size:10px">ABIERTA</span>'
      : '<span class="badge badge-stopped" style="font-size:10px">CERRADA</span>';
    const reason = t.exit_reason
      ? `<span class="neutral" style="font-size:11px">${t.exit_reason.split(':')[0]}</span>`
      : '—';

    return `
      <tr>
        <td><strong>${t.asset}</strong> <small class="neutral">${t.window}</small></td>
        <td>${t.side}</td>
        <td>${formatUSDC(t.amount)}</td>
        <td>${t.entry_price.toFixed(4)}</td>
        <td>${t.exit_price != null ? t.exit_price.toFixed(4) : '—'}</td>
        <td>${pnlText}</td>
        <td>${reason}</td>
        <td>${statusBadge}</td>
      </tr>
    `;
  }).join('');
}

function toggleOpenOnly() {
  state.openOnly = document.getElementById('open-only-toggle').checked;
  fetchTrades();
}

// ── Utilidades ────────────────────────────────────────────────────────
async function apiFetch(url) {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (e) {
    console.warn(`apiFetch error [${url}]:`, e.message);
    return null;
  }
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setColoredText(id, text, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className   = 'kpi-value ' + (value > 0 ? 'positive' : value < 0 ? 'negative' : '');
}

function formatUSDC(v) {
  if (v == null) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

function formatPct(v) {
  if (v == null) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${(v * 100).toFixed(2)}%`;
}

function formatVolume(v) {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000)     return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}