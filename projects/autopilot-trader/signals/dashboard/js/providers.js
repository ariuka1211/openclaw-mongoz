function startPolling(key, url, intervalMs) {
  async function tick() {
    try {
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        Store.set(key, data);
      }
    } catch (e) {
      console.warn(`[providers] ${key} fetch failed:`, e.message);
    }
  }
  tick();
  return setInterval(tick, intervalMs);
}

function startAllProviders() {
  // Portfolio — fast refresh
  startPolling('portfolio', '/api/portfolio', 5000);
  startPolling('portfolioSummary', '/api/portfolio/summary', 5000);

  // AI Trader
  startPolling('traderStatus', '/api/trader/status', 10000);
  startPolling('decisions', '/api/trader/decisions?n=30', 10000);
  startPolling('traderAlerts', '/api/trader/alerts?limit=20', 10000);

  // Performance
  startPolling('performance', '/api/trader/performance', 30000);
  startPolling('equityCurve', '/api/trader/equity-curve', 30000);
  startPolling('confidenceStats', '/api/trader/confidence-stats', 60000);
  startPolling('perSymbol', '/api/trader/per-symbol', 60000);
  startPolling('byExitReason', '/api/trader/by-exit-reason', 60000);

  // Scanner
  startPolling('scannerOpps', '/api/scanner/opportunities?n=15', 60000);
  startPolling('scannerFunding', '/api/scanner/funding', 60000);
  startPolling('scannerDistribution', '/api/scanner/distribution', 60000);
  startPolling('scannerStats', '/api/scanner/stats', 60000);

  // System
  startPolling('systemHealth', '/api/system/health', 30000);
  startPolling('systemErrors', '/api/system/errors', 30000);
}
