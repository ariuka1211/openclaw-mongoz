# Browser Automation Skill

**Created:** 2026-04-07  
**Status:** Active  
**Location:** `~/.openclaw/workspace/skills/browser-automation/`

## Overview
Unified browser automation skill that intelligently selects the optimal tool for each task, solving the "tool sprawl" problem where multiple overlapping browser tools created confusion and inefficiency.

## Tool Selection Logic

Smart auto-selection based on task analysis:

```
Simple fetch → Browserbase (0.9s, cloud API)
Complex automation → Agent Browser (AI-driven, forms)  
Stealth required → Playwright (anti-detection)
Mobile testing → Agent Browser iOS
Cost-sensitive → GSD Browser (local, free)
```

## Components

### Core Files
- `SKILL.md` — Unified interface documentation
- `scripts/browser-automation.py` — Smart tool selector with fallbacks
- `configs/browser-automation.json` — Configuration for all 4 tools

### Reference Documentation  
- `references/browserbase-api.md` — Cloud API patterns
- `references/agent-browser-advanced.md` — AI automation guide
- `references/playwright-stealth.md` — Anti-detection techniques
- `references/gsd-browser-config.md` — Local execution setup

## Usage Examples

```bash
# Auto-selects Browserbase for speed
browser-automation fetch "https://coinglass.com/liquidations/BTC"

# Auto-selects Agent Browser for complexity
browser-automation complex "login to exchange and check portfolio"

# Force specific tool
browser-automation --force-tool=playwright --stealth fetch "protected-site.com"
```

## Integration

Integrates with existing OpenClaw tools:
- **web_fetch fallback** — Tries browser automation if web_fetch fails
- **Memory system** — Saves successful patterns
- **Message system** — Sends alerts via OpenClaw channels
- **File handling** — Screenshots/downloads to workspace

## Key Innovation

**Automatic fallbacks:** If primary tool fails, tries alternatives:
- Browserbase fails → Try Agent Browser → Try GSD Browser
- Rate limited → Switch to local execution
- Bot detected → Enable stealth mode

## Cross-References
- [[Browser Automation Comparison]] — Tool evaluation results
- [[Browserbase API]] — Cloud browser service  
- [[Agent Browser Guide]] — AI-native automation
- [[Trading Data Sources]] — CoinGlass integration patterns