# Browser Tool Audit & Strategy

## Current Tools (The Mess)
1. **Browserbase** (✅ Active) - Cloud API for simple fetching
2. **GSD Browser** (⚠️ Complex) - Local automation, slow setup  
3. **Playwright** (🤔 Unused) - Installed in V2 bot, stealth features
4. **Agent Browser** (🆕 New) - Vercel's AI browser automation

## Overlaps & Redundancy
- All can scrape websites
- All handle JavaScript  
- All require different APIs/setup
- No unified interface

## Documentation Status
❌ **Missing**: Unified browser skill/documentation
❌ **Scattered**: Configs across multiple locations
❌ **No examples**: Actual trading use cases

## Proposed Solution: Browser Skill
Create `~/.openclaw/workspace/skills/browser-automation/SKILL.md`:

```
├── browser-automation/
│   ├── SKILL.md              # Master skill file  
│   ├── configs/
│   │   ├── browserbase.json  # API config
│   │   ├── gsd.json         # Local config
│   │   └── playwright.json   # Stealth config
│   ├── examples/
│   │   ├── coinglass.py     # Liquidation scraping
│   │   ├── tradingview.py   # Chart data
│   │   └── news.py          # Market signals
│   └── scripts/
│       ├── choose-tool.py    # Auto-select best tool
│       └── fallback.py       # Tool switching logic
```

## Strategy: Smart Tool Selection
Instead of manual choice, auto-select based on task:
- **Simple fetch** → Browserbase (fastest)
- **Complex forms** → GSD Browser  
- **Stealth needed** → Playwright
- **AI-driven** → Agent Browser

## Action Items
1. ✅ Keep Browserbase (primary)
2. ⚠️ Document GSD Browser properly
3. 🤔 Test Playwright integration
4. 🆕 Evaluate Agent Browser
5. 📚 Create unified skill

Want me to create the unified browser skill?