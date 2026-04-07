---
name: browser-automation
description: Unified browser automation for web scraping, data extraction, form filling, and complex web interactions. Use for: (1) scraping dynamic websites with JavaScript, (2) automated testing and QA, (3) form automation and login flows, (4) mobile testing, (5) data extraction from trading/finance sites like CoinGlass, TradingView, (6) monitoring and alerts, (7) when simple API calls or web_fetch are insufficient for complex pages. Automatically selects optimal tool: Browserbase (simple/fast), Agent Browser (complex/AI-driven), GSD Browser (local/free), or Playwright (stealth).
---

# Browser Automation Skill

Unified interface for browser automation that automatically selects the best tool for each task.

## Quick Start

```bash
# The skill auto-selects the best tool:
browser-automation fetch "https://coinglass.com/liquidations/BTC"          # → Browserbase
browser-automation login "gmail.com" "user@example.com" "password"        # → Agent Browser  
browser-automation complex "fill out job application on company.com"      # → Agent Browser
browser-automation screenshot "https://tradingview.com/chart" mobile      # → Agent Browser iOS
browser-automation stealth "scrape protected site with bot detection"     # → Playwright
```

## Tool Selection Logic

The skill automatically chooses the optimal tool:

| Task Type | Tool | When Used |
|-----------|------|-----------|
| **Simple Fetch** | Browserbase | Single page, no interaction, pure data extraction |
| **AI-Driven Tasks** | Agent Browser | Complex workflows, natural language instructions |
| **Stealth Required** | Playwright | Bot detection, anti-scraping measures |
| **Mobile Testing** | Agent Browser iOS | Real mobile Safari testing |
| **Cost-Sensitive** | GSD Browser | Local execution, no API costs |

## Available Commands

### 1. Simple Data Fetching
```bash
# Quick page fetch (auto-uses Browserbase)
browser-automation fetch <url> [--wait=3000] [--format=html|json]

# Extract specific data  
browser-automation extract <url> <css-selector> [--attribute=text|href|src]
```

### 2. Complex Automation  
```bash
# Natural language automation (uses Agent Browser)
browser-automation complex "<instruction>"
browser-automation login <site> <username> <password> [--2fa]
browser-automation form <url> <field1=value1> <field2=value2>
```

### 3. Testing & QA
```bash
# Screenshot comparison
browser-automation screenshot <url> [--mobile] [--annotate] [--diff=baseline.png]

# Mobile testing 
browser-automation mobile <device> <url> <action>
```

### 4. Monitoring & Alerts
```bash
# Watch for changes
browser-automation monitor <url> <selector> [--interval=60] [--alert=telegram]

# Performance testing
browser-automation perf <url> [--metrics=load,render,interactive]
```

## Tool-Specific Features

### Browserbase Features
- ✅ Fastest execution (~0.9s)
- ✅ Zero setup required
- ✅ Managed infrastructure
- ✅ JavaScript rendering
- ❌ No complex interactions
- ❌ No session persistence

### Agent Browser Features  
- ✅ AI-native commands
- ✅ Complex multi-step workflows
- ✅ Session management
- ✅ Mobile Safari testing
- ✅ Network interception
- ✅ Screenshot annotation
- ⚠️ Requires installation

### GSD Browser Features
- ✅ Free local execution
- ✅ Full Chrome automation
- ✅ Custom configurations
- ⚠️ Slower performance
- ⚠️ Complex setup

### Playwright Features
- ✅ Stealth capabilities
- ✅ Cross-browser support
- ✅ Network mocking
- ✅ Mature ecosystem
- ⚠️ Anti-detection setup required

## Configuration

The skill reads configuration from `configs/browser-automation.json`:

```json
{
  "browserbase": {
    "apiKey": "bb_live_...",
    "defaultTimeout": 3000,
    "endpoint": "https://api.browserbase.com/v1/fetch"
  },
  "agentBrowser": {
    "defaultDevice": "iPhone 16 Pro",
    "sessionDir": "~/.agent-browser/sessions",
    "headless": true
  },
  "gsdBrowser": {
    "chromePath": "/usr/bin/google-chrome",
    "downloadPath": "./downloads"
  },
  "playwright": {
    "stealthMode": true,
    "blockAds": true,
    "userAgent": "custom-agent/1.0"
  },
  "selection": {
    "preferLocal": false,
    "costPriority": "speed",
    "defaultTool": "auto"
  }
}
```

## Usage Examples

### Trading Data Extraction
```bash
# CoinGlass liquidations (Browserbase - fast & simple)
browser-automation fetch "https://coinglass.com/liquidations/BTC" --format=json

# TradingView complex analysis (Agent Browser - JavaScript heavy)
browser-automation complex "navigate to TradingView, open BTC chart, add RSI indicator, screenshot"

# Multiple exchange comparison (batch processing)
browser-automation batch \
  "fetch binance.com/api/v3/ticker/24hr" \
  "fetch coinbase.com/api/v2/exchange-rates" \
  "extract kraken.com '.price-display' --attribute=text"
```

### Form Automation
```bash
# Simple contact form (Agent Browser)
browser-automation form "example.com/contact" \
  name="John Doe" \
  email="john@example.com" \
  message="Hello world"

# Complex multi-page workflow  
browser-automation complex "Go to jobs.company.com, search for 'Software Engineer', apply to first posting with my resume from ~/resume.pdf"
```

### Mobile Testing
```bash
# iOS Safari testing
browser-automation mobile "iPhone 16 Pro" "mobile-app.com" \
  "tap login, fill email, fill password, submit, verify dashboard loads"

# Responsive design check
browser-automation screenshot "website.com" --mobile --compare=desktop.png
```

### Monitoring & Alerts
```bash
# Price monitoring
browser-automation monitor "coinglass.com/liquidations/BTC" ".total-liquidations" \
  --interval=300 \
  --alert="telegram:-1002381931352" \
  --threshold="100M"

# Site uptime monitoring
browser-automation monitor "my-trading-bot.com/health" "body" \
  --interval=60 \
  --alert="email:alerts@mybot.com" \
  --expect="OK"
```

## Tool Installation

The skill auto-installs missing tools on first use:

```bash
# Browserbase (no installation needed - API only)
# API key stored in configs/browserbase.env

# Agent Browser (recommended for complex tasks)
npm install -g agent-browser
agent-browser install

# GSD Browser (free alternative) 
curl -L -o gsd-browser https://github.com/gsd-build/gsd-browser/releases/download/v0.1.3/gsd-browser-linux-x64
chmod +x gsd-browser

# Playwright (stealth mode)
pip install playwright playwright-stealth
playwright install chromium
```

## Advanced Features

### Session Management
```bash
# Named sessions for persistence
browser-automation --session=trading-bot login "exchange.com" "user" "pass"
browser-automation --session=trading-bot complex "place limit order BTC 45000"

# State persistence 
browser-automation --save-state=./exchange-auth.json login "exchange.com" "user" "pass"
browser-automation --load-state=./exchange-auth.json complex "check portfolio balance"
```

### Network Control
```bash
# Block ads/trackers (Playwright)
browser-automation --stealth --block-ads fetch "news-site.com"

# Mock API responses (Agent Browser)
browser-automation --mock="api.example.com/data={mock_response.json}" fetch "app.com"

# Proxy support
browser-automation --proxy="socks5://proxy.example.com:1080" fetch "geo-restricted-site.com"
```

### Performance Optimization
```bash
# Concurrent execution
browser-automation batch --parallel=3 \
  "fetch site1.com" \
  "fetch site2.com" \
  "fetch site3.com"

# Caching
browser-automation --cache=60 fetch "slow-api.com/data"  # Cache for 60 seconds

# Resource blocking
browser-automation --block="images,fonts" fetch "data-only-site.com"
```

## Error Handling & Fallbacks

The skill implements automatic fallbacks:

1. **Primary tool fails** → Try secondary tool
2. **Rate limited** → Switch to local tool  
3. **Captcha detected** → Use stealth mode
4. **Mobile required** → Switch to Agent Browser iOS
5. **Complex JS** → Upgrade from Browserbase to Agent Browser

```bash
# Manual fallback override
browser-automation --force-tool=playwright fetch "difficult-site.com"
browser-automation --fallback=gsd-browser,playwright complex "handle bot detection"
```

## Debugging & Monitoring

```bash
# Debug mode (show tool selection reasoning)
browser-automation --debug fetch "example.com"

# Performance timing
browser-automation --timing complex "multi-step workflow"

# Live monitoring dashboard
browser-automation dashboard start --port=8080
# Open http://localhost:8080 to see real-time browser sessions
```

## Integration with OpenClaw

The skill integrates with OpenClaw's existing tools:

- **web_fetch fallback**: Automatically tries browser automation if web_fetch fails
- **Memory integration**: Saves successful scraping patterns to memory
- **Notification**: Sends alerts via OpenClaw message system
- **File handling**: Saves screenshots/downloads to workspace
- **Session persistence**: Stores auth states in OpenClaw workspace

## Security & Best Practices

### Authentication Security
- Credentials stored in encrypted vault (`browser-automation auth save`)
- Session tokens auto-expire after 30 days
- No credentials in logs or command history

### Rate Limiting
- Automatic throttling to avoid being blocked
- Respects robots.txt when specified
- Built-in delays between requests

### Resource Management  
- Auto-cleanup of temporary files
- Browser session limits (max 3 concurrent)
- Memory usage monitoring and alerts

## References

For detailed tool-specific documentation:
- **Browserbase API**: See `references/browserbase-api.md`
- **Agent Browser Guide**: See `references/agent-browser-advanced.md`
- **Playwright Stealth**: See `references/playwright-stealth.md`
- **GSD Browser**: See `references/gsd-browser-config.md`
- **Mobile Testing**: See `references/mobile-testing.md`
- **Performance Tuning**: See `references/performance.md`

## Troubleshooting

Common issues and solutions:
- **"Tool not found"**: Run `browser-automation install <tool>`
- **"Captcha detected"**: Use `--stealth` or `--force-tool=playwright`
- **"Timeout"**: Increase `--wait=<milliseconds>` or use `--tool=agent-browser`
- **"Rate limited"**: Switch to `--tool=gsd-browser` for local execution
- **"Mobile not working"**: Ensure Xcode installed and iOS Simulator available

Load detailed troubleshooting with: `browser-automation help troubleshoot`