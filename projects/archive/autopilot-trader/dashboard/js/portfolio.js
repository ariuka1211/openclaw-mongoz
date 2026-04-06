const PortfolioTab = {
  render() {
    Store.subscribe('portfolio', data => this.renderPositions(data));
    Store.subscribe('portfolioSummary', data => this.renderSummary(data));
  },

  renderSummary(s) {
    if (!s) return;
    const el = id => document.getElementById(id);
    el('p-equity').textContent = s.equity != null ? '$' + fmt(s.equity) : '—';
    el('p-positions-count').textContent = s.position_count ?? '—';
    el('p-positions-max').textContent = s.max_concurrent ?? '—';
    el('p-exposure').textContent = s.total_exposure_usd != null ? '$' + fmt(s.total_exposure_usd) : '—';
    el('p-exposure').className = 'stat-value ' + (s.total_exposure_usd > 0 ? 'short' : '');
    el('p-last-update').textContent = 'Updated: ' + new Date().toLocaleTimeString();
  },

  renderPositions(data) {
    if (!data || !data.positions) return;
    const tbody = document.getElementById('p-positions-body');
    const positions = data.positions;

    if (positions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty-row">No active positions</td></tr>';
      return;
    }

    tbody.innerHTML = positions.map(p => {
      const sideClass = p.side === 'long' ? 'long' : 'short';
      const sideBadge = p.side === 'long'
        ? '<span class="badge badge-green">LONG</span>'
        : '<span class="badge badge-red">SHORT</span>';
      const pnlSign = p.unrealized_pnl >= 0 ? '+' : '';
      const roeSign = p.roe_pct >= 0 ? '+' : '';
      const pnlClass = p.unrealized_pnl >= 0 ? 'long' : 'short';
      const dslTier = p.dsl?.current_tier_trigger || '—';
      const breaches = p.dsl?.breach_count ?? 0;
      const breachColor = breaches > 0 ? 'var(--red)' : 'var(--muted)';

      return `<tr>
        <td><strong>${esc(p.symbol)}</strong></td>
        <td>${sideBadge}</td>
        <td>${priceFmt(p.entry_price)}</td>
        <td>${priceFmt(p.current_price)}</td>
        <td class="${pnlClass}">${pnlSign}$${fmt(p.unrealized_pnl)}</td>
        <td class="${pnlClass}">${roeSign}${fmt(p.roe_pct)}%</td>
        <td>${fmt(p.leverage, 0)}×</td>
        <td>${esc(String(dslTier))}</td>
        <td style="color:${breachColor}">${breaches}</td>
      </tr>`;
    }).join('');

    // Update exposure from positions data
    if (data.total_exposure_usd != null) {
      document.getElementById('p-exposure').textContent = '$' + fmt(data.total_exposure_usd);
    }
  }
};
