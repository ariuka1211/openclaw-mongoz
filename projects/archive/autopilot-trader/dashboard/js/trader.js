const TraderTab = {
  render() {
    Store.subscribe('traderStatus', data => this.renderStatus(data));
    Store.subscribe('decisions', data => this.renderDecisions(data));
  },

  renderStatus(s) {
    if (!s) return;
    const el = id => document.getElementById(id);

    el('t-last-cycle').textContent = s.last_cycle_ago || '—';
    el('t-model').textContent = s.model || '—';

    const aliveBadge = s.alive
      ? '<span class="badge badge-green">ONLINE</span>'
      : '<span class="badge badge-red">OFFLINE</span>';
    el('t-status').innerHTML = aliveBadge;
  },

  renderDecisions(data) {
    if (!data || !Array.isArray(data)) return;
    const tbody = document.getElementById('t-decisions-body');

    if (data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-row">No decisions yet</td></tr>';
      return;
    }

    // Count today's decisions
    const today = new Date().toISOString().slice(0, 10);
    const todayCount = data.filter(d => d.timestamp && d.timestamp.startsWith(today)).length;
    document.getElementById('t-decisions-today').textContent = todayCount;

    // Count safety rejections (last 30 min)
    const thirtyMinAgo = Date.now() - 30 * 60 * 1000;
    const rejections = data.filter(d => {
      if (d.safety_approved) return false;
      try { return new Date(d.timestamp).getTime() > thirtyMinAgo; } catch { return false; }
    }).length;
    document.getElementById('t-rejections').textContent = rejections;

    // Avg latency
    const latencies = data.filter(d => d.latency_ms).map(d => d.latency_ms);
    const avgLat = latencies.length > 0
      ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length)
      : null;
    document.getElementById('t-latency').textContent = avgLat != null ? avgLat + 'ms' : '—';

    tbody.innerHTML = data.map(d => {
      const time = d.timestamp ? new Date(d.timestamp).toLocaleTimeString() : '—';
      const actionBadge = actionBadgeHtml(d.action);
      const dirBadge = d.direction
        ? (d.direction === 'long'
          ? '<span class="badge badge-green">LONG</span>'
          : '<span class="badge badge-red">SHORT</span>')
        : '—';
      const confBar = d.confidence != null
        ? `<div class="score-bar"><div class="score-fill" style="width:${Math.round(d.confidence * 100)}px;background:${confColor(d.confidence)}"></div><span style="font-size:12px">${fmt(d.confidence, 2)}</span></div>`
        : '—';
      const safety = d.safety_approved === true
        ? '<span style="color:var(--green)">✓</span>'
        : '<span style="color:var(--red)">✗</span>';
      const executed = d.executed === true
        ? '<span style="color:var(--green)">✓</span>'
        : '<span style="color:var(--muted)">—</span>';

      return `<tr>
        <td style="font-variant-numeric:tabular-nums">${time}</td>
        <td>${actionBadge}</td>
        <td><strong>${esc(d.symbol || '—')}</strong></td>
        <td>${dirBadge}</td>
        <td>${confBar}</td>
        <td>${safety}</td>
        <td>${executed}</td>
        <td>${d.latency_ms ? d.latency_ms + 'ms' : '—'}</td>
      </tr>`;
    }).join('');
  }
};

function actionBadgeHtml(action) {
  const map = {
    open: '<span class="badge badge-green">OPEN</span>',
    close: '<span class="badge badge-red">CLOSE</span>',
    hold: '<span class="badge badge-yellow">HOLD</span>',
    adjust: '<span class="badge badge-blue">ADJUST</span>',
  };
  return map[action] || `<span class="badge badge-blue">${esc(String(action || '').toUpperCase())}</span>`;
}

function confColor(c) {
  if (c >= 0.7) return 'var(--green)';
  if (c >= 0.4) return 'var(--yellow)';
  return 'var(--red)';
}
