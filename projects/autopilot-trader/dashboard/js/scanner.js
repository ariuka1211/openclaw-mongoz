const ScannerTab = {
  distChart: null,

  render() {
    Store.subscribe('scannerOpps', data => this.renderOpps(data));
    Store.subscribe('scannerStats', data => this.renderStats(data));
    Store.subscribe('scannerDistribution', data => this.renderDistribution(data));
    Store.subscribe('scannerFunding', data => this.renderFunding(data));
  },

  renderStats(s) {
    if (!s) return;
    document.getElementById('sc-total').textContent = s.total ?? '—';

    if (s.age_seconds != null) {
      const mins = Math.floor(s.age_seconds / 60);
      const secs = s.age_seconds % 60;
      const text = mins > 0 ? `${mins}m ${secs}s ago` : `${secs}s ago`;
      document.getElementById('sc-age').textContent = text;
      // Staleness indicator
      const staleness = s.age_seconds > 600 ? 'var(--red)' : s.age_seconds > 300 ? 'var(--yellow)' : 'var(--green)';
      document.getElementById('sc-age').style.color = staleness;
    }
  },

  renderOpps(data) {
    if (!data || !Array.isArray(data)) return;
    const tbody = document.getElementById('sc-opps-body');

    if (data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty-row">No opportunities</td></tr>';
      return;
    }

    // Avg composite score
    const avgScore = data.length > 0
      ? data.reduce((a, o) => a + (o.compositeScore || 0), 0) / data.length
      : 0;
    document.getElementById('sc-avg-score').textContent = fmt(avgScore, 1);

    tbody.innerHTML = data.map((o, i) => {
      const dirBadge = o.direction === 'long'
        ? '<span class="badge badge-green">LONG</span>'
        : '<span class="badge badge-red">SHORT</span>';
      const score = Math.round(o.compositeScore || 0);
      const scoreColor = score >= 65 ? 'var(--green)' : score >= 50 ? 'var(--yellow)' : 'var(--muted)';

      // Component sub-scores
      const fs = o.fundingSpreadScore ?? '—';
      const va = o.volumeAnomalyScore ?? '—';
      const mo = o.momentumScore ?? '—';
      const ma = o.maAlignmentScore ?? '—';
      const ob = o.orderBlockScore ?? '—';

      return `<tr>
        <td style="color:var(--muted)">${i + 1}</td>
        <td><strong>${esc(o.symbol)}</strong></td>
        <td>${dirBadge}</td>
        <td>
          <div class="score-bar">
            <div class="score-fill" style="width:${score}px;background:${scoreColor}"></div>
            <span style="font-size:12px">${score}</span>
          </div>
        </td>
        <td class="sub-score">${fs}</td>
        <td class="sub-score">${va}</td>
        <td class="sub-score">${mo}</td>
        <td class="sub-score">${ma}</td>
        <td class="sub-score">${ob}</td>
      </tr>`;
    }).join('');
  },

  renderDistribution(data) {
    if (!data || !data.buckets) return;

    const ctx = document.getElementById('sc-dist-chart').getContext('2d');
    const labels = Object.keys(data.buckets);
    const values = Object.values(data.buckets);

    if (this.distChart) this.distChart.destroy();

    this.distChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Opportunities',
          data: values,
          backgroundColor: '#58a6ff',
          borderRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            ticks: { color: '#8b949e', font: { size: 10 } },
            grid: { display: false }
          },
          y: {
            ticks: { color: '#8b949e', stepSize: 1 },
            grid: { color: '#21262d' }
          }
        }
      }
    });

    // Component averages
    const ca = data.component_averages || {};
    const caEl = document.getElementById('sc-component-averages');
    if (Object.keys(ca).length > 0) {
      const labels = {
        fundingSpreadScore: 'Funding',
        volumeAnomalyScore: 'Volume',
        momentumScore: 'Momentum',
        maAlignmentScore: 'MA',
        orderBlockScore: 'OB',
      };
      caEl.innerHTML = Object.entries(ca).map(([k, v]) =>
        `<span class="stat-label">${labels[k] || k}:</span> <span class="stat-value">${fmt(v, 1)}</span>`
      ).join(' &nbsp;|&nbsp; ');
    }
  },

  renderFunding(data) {
    if (!data || !Array.isArray(data)) return;
    const tbody = document.getElementById('sc-funding-body');

    if (data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty-row">No funding data</td></tr>';
      return;
    }

    tbody.innerHTML = data.slice(0, 10).map(f => {
      const spread = f.fundingSpread8h || 0;
      const spreadAbs = Math.abs(spread);
      const spreadClass = spread > 0 ? 'long' : spread < 0 ? 'short' : '';
      const dir = spread > 0
        ? '<span class="badge badge-green">LONG</span>'
        : spread < 0
          ? '<span class="badge badge-red">SHORT</span>'
          : '—';

      return `<tr>
        <td><strong>${esc(f.symbol)}</strong></td>
        <td>${f.lighterFundingRate8h != null ? fmt(f.lighterFundingRate8h * 100, 2) + '%' : '—'}</td>
        <td>${f.cexAvgFundingRate8h != null ? fmt(f.cexAvgFundingRate8h * 100, 2) + '%' : '—'}</td>
        <td class="${spreadClass}">${spread >= 0 ? '+' : ''}${fmt(spreadAbs * 100, 2)}%</td>
      </tr>`;
    }).join('');
  }
};
