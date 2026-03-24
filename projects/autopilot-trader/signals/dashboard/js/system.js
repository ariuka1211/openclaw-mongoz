const SystemTab = {
  render() {
    Store.subscribe('systemHealth', data => this.renderHealth(data));
    Store.subscribe('systemErrors', data => this.renderErrors(data));
  },

  renderHealth(h) {
    if (!h || !h.services) return;
    const tbody = document.getElementById('sys-services-body');

    const services = h.services || {};
    const rows = [];

    // Bot
    const bot = services.bot || {};
    rows.push(this.serviceRow('Bot', bot.running, bot.pid, bot.last_state_ago));

    // AI Trader
    const ai = services.ai_trader || {};
    rows.push(this.serviceRow('AI Trader', ai.running, ai.pid, ai.last_cycle_ago, ai.model));

    // Scanner
    const scanner = services.scanner || {};
    rows.push(this.serviceRow('Scanner', scanner.running, scanner.pid, scanner.last_scan_ago, scanner.stale ? '⚠ STALE' : null));

    // Dashboard
    const dash = h.dashboard || {};
    const dashUptime = dash.uptime_seconds != null ? formatUptime(dash.uptime_seconds) : '—';
    rows.push(`<tr>
      <td><strong>Dashboard</strong></td>
      <td><span class="status-dot on"></span></td>
      <td>—</td>
      <td>—</td>
      <td>${esc(dashUptime)}</td>
    </tr>`);

    tbody.innerHTML = rows.join('');
  },

  serviceRow(name, running, pid, lastActivity, extra) {
    const statusDot = running
      ? '<span class="status-dot on"></span>'
      : '<span class="status-dot off"></span>';
    const pidStr = pid || '—';
    const extraStr = extra ? ` <span style="color:var(--yellow);font-size:11px">${esc(String(extra))}</span>` : '';

    return `<tr>
      <td><strong>${esc(name)}</strong>${extraStr}</td>
      <td>${statusDot}</td>
      <td style="font-variant-numeric:tabular-nums">${pidStr}</td>
      <td>${esc(lastActivity || '—')}</td>
      <td>—</td>
    </tr>`;
  },

  renderErrors(data) {
    if (!data || !Array.isArray(data)) return;
    const list = document.getElementById('sys-errors-list');

    if (data.length === 0) {
      list.innerHTML = '<div style="color:var(--muted)">No recent errors ✓</div>';
      return;
    }

    list.innerHTML = data.map(e => {
      const time = e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : '—';
      const level = e.level || 'ERROR';
      const levelColor = level === 'WARN' ? 'var(--yellow)' : level === 'ERROR' ? 'var(--red)' : 'var(--muted)';

      return `<div class="error-item">
        <span class="activity-time">${esc(time)}</span>
        <span style="color:${levelColor};font-weight:600;font-size:11px">${esc(level)}</span>
        <span>${esc(e.message || e.text || '')}</span>
      </div>`;
    }).join('');
  }
};

function formatUptime(seconds) {
  if (seconds < 60) return seconds + 's';
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return mins + 'm';
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}
