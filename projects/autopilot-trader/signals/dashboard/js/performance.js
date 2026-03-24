const PerformanceTab = {
  equityChart: null,
  confChart: null,

  render() {
    Store.subscribe('performance', data => this.renderPerf(data));
    Store.subscribe('equityCurve', data => this.renderEquityChart(data));
    Store.subscribe('confidenceStats', data => this.renderConfChart(data));
    Store.subscribe('perSymbol', data => this.renderPerSymbol(data));
    Store.subscribe('byExitReason', data => this.renderByExit(data));
  },

  renderPerf(p) {
    if (!p || p.error) return;
    const el = id => document.getElementById(id);

    el('pf-winrate').textContent = p.win_rate != null ? fmt(p.win_rate, 1) + '%' : '—';
    el('pf-trades-count').textContent = p.total_trades ?? '';

    el('pf-total-pnl').textContent = p.total_pnl != null
      ? (p.total_pnl >= 0 ? '+' : '') + '$' + fmt(p.total_pnl)
      : '—';
    el('pf-total-pnl').className = 'stat-value ' + (p.total_pnl >= 0 ? 'long' : 'short');

    // Compute win/loss ratio from avg_win and avg_loss
    const winLossRatio = (p.avg_loss && p.avg_loss !== 0)
      ? p.avg_win / p.avg_loss
      : null;
    el('pf-winloss').textContent = winLossRatio != null ? fmt(winLossRatio, 2) : '—';

    el('pf-maxdd').textContent = p.max_drawdown != null
      ? '$' + fmt(p.max_drawdown)
      : '—';

    // Direction breakdown — hide if no data
    const byDir = p.by_direction;
    const dirSection = document.getElementById('pf-direction-card');
    const dirBody = document.getElementById('pf-direction-body');
    if (!byDir || Object.keys(byDir).length === 0) {
      if (dirSection) dirSection.style.display = 'none';
    } else {
      if (dirSection) dirSection.style.display = '';
      dirBody.innerHTML = Object.entries(byDir).map(([dir, stats]) => {
        const badge = dir === 'long'
          ? '<span class="badge badge-green">LONG</span>'
          : '<span class="badge badge-red">SHORT</span>';
        const pnlSign = stats.total_pnl >= 0 ? '+' : '';
        const pnlClass = stats.total_pnl >= 0 ? 'long' : 'short';
        return `<tr>
          <td>${badge}</td>
          <td>${stats.trades ?? 0}</td>
          <td class="${pnlClass}">${pnlSign}$${fmt(stats.total_pnl ?? 0)}</td>
          <td>${stats.win_rate != null ? fmt(stats.win_rate, 0) + '%' : '—'}</td>
        </tr>`;
      }).join('') || '<tr><td colspan="4" class="empty-row">No data</td></tr>';
    }
  },

  renderEquityChart(data) {
    if (!data || !Array.isArray(data) || data.length === 0) return;

    const ctx = document.getElementById('pf-equity-chart').getContext('2d');
    const labels = data.map(d => {
      try { return new Date(d.timestamp).toLocaleString(); } catch { return ''; }
    });
    const values = data.map(d => d.cumulative_pnl);

    if (this.equityChart) this.equityChart.destroy();

    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, 'rgba(63, 185, 80, 0.3)');
    gradient.addColorStop(1, 'rgba(63, 185, 80, 0)');

    this.equityChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Cumulative PnL',
          data: values,
          borderColor: '#3fb950',
          backgroundColor: gradient,
          fill: true,
          tension: 0.3,
          pointRadius: 3,
          pointBackgroundColor: values.map(v => v >= 0 ? '#3fb950' : '#f85149'),
          borderWidth: 2,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => 'PnL: $' + fmt(ctx.parsed.y)
            }
          }
        },
        scales: {
          x: {
            ticks: { color: '#8b949e', maxTicksLimit: 8, font: { size: 10 } },
            grid: { color: '#21262d' }
          },
          y: {
            ticks: {
              color: '#8b949e',
              callback: v => '$' + fmt(v)
            },
            grid: { color: '#21262d' }
          }
        }
      }
    });
  },

  renderConfChart(data) {
    if (!data || data.error) return;

    const ctx = document.getElementById('pf-confidence-chart').getContext('2d');
    const stats = data.confidence_stats || data;

    // Try to extract bracket data
    let brackets = [];
    if (Array.isArray(stats)) {
      brackets = stats;
    } else if (stats.brackets) {
      brackets = stats.brackets;
    } else {
      // Build from available data
      const keys = Object.keys(stats);
      for (const k of keys) {
        const s = stats[k];
        if (s && typeof s === 'object' && s.win_rate !== undefined) {
          brackets.push({ bracket: k, win_rate: s.win_rate, trades: s.trades });
        }
      }
    }

    if (brackets.length === 0) return;

    if (this.confChart) this.confChart.destroy();

    this.confChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: brackets.map(b => b.bracket || b.label || '—'),
        datasets: [{
          label: 'Win Rate %',
          data: brackets.map(b => b.win_rate ?? 0),
          backgroundColor: brackets.map(b =>
            (b.win_rate ?? 0) >= 60 ? '#3fb950' :
            (b.win_rate ?? 0) >= 40 ? '#d29922' : '#f85149'
          ),
          borderRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            ticks: { color: '#8b949e' },
            grid: { display: false }
          },
          y: {
            min: 0, max: 100,
            ticks: { color: '#8b949e', callback: v => v + '%' },
            grid: { color: '#21262d' }
          }
        }
      }
    });
  },

  renderPerSymbol(data) {
    if (!data || !Array.isArray(data)) return;
    const tbody = document.getElementById('pf-symbol-body');

    if (data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty-row">No trade data</td></tr>';
      return;
    }

    tbody.innerHTML = data.map(s => {
      const pnlSign = s.total_pnl >= 0 ? '+' : '';
      const pnlClass = s.total_pnl >= 0 ? 'long' : 'short';
      const holdMin = s.avg_hold_seconds != null ? Math.round(s.avg_hold_seconds / 60) + 'min' : '—';
      return `<tr>
        <td><strong>${esc(s.symbol)}</strong></td>
        <td>${s.trades}</td>
        <td class="${pnlClass}">${pnlSign}$${fmt(s.total_pnl)}</td>
        <td class="${pnlClass}">${pnlSign}$${fmt(s.avg_pnl)}</td>
        <td>${holdMin}</td>
      </tr>`;
    }).join('');
  },

  renderByExit(data) {
    if (!data || !Array.isArray(data)) return;
    const tbody = document.getElementById('pf-exit-body');

    if (data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty-row">No exit data</td></tr>';
      return;
    }

    tbody.innerHTML = data.map(e => {
      const pnlSign = e.total_pnl >= 0 ? '+' : '';
      const pnlClass = e.total_pnl >= 0 ? 'long' : 'short';
      return `<tr>
        <td>${esc(e.exit_reason || '—')}</td>
        <td>${e.trades}</td>
        <td class="${pnlClass}">${pnlSign}$${fmt(e.total_pnl)}</td>
        <td class="${pnlClass}">${pnlSign}$${fmt(e.avg_pnl)}</td>
      </tr>`;
    }).join('');
  }
};
