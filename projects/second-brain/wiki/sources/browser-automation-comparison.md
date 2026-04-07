# Browser Automation Comparison 2026

**Source:** OpenClaw session 2026-04-07  
**Tags:** #browser-automation #web-scraping #trading-tools

## Summary
Comprehensive evaluation of 4 browser automation tools for trading data extraction and complex web workflows. Created unified [[Browser Automation Skill]] to solve tool sprawl.

## Tool Comparison Results

### Browserbase (Cloud API) ⭐
- **Speed**: 0.92s per request
- **Setup**: Zero (pure API)
- **Auth**: `x-bb-api-key` header (critical detail)
- **Best for**: Simple data fetching, high-frequency scraping
- **Limitations**: No complex interactions, session management
- **Status**: ✅ Working for CoinGlass liquidation data

### Agent Browser (AI-Native) 🤖
- **Speed**: Variable (2-30s depending on complexity)
- **Setup**: `npm i -g agent-browser` + Chrome download
- **Best for**: AI-driven workflows, form automation, mobile testing
- **API**: `snapshot -i` → refs (`@e1`) → interact pattern
- **Limitations**: Bot detection issues, blocked by CoinGlass/Google
- **Status**: ✅ Installed, good for complex tasks

### GSD Browser (Local Free)
- **Speed**: ~6.3s (6x slower than Browserbase)
- **Setup**: Binary + Chrome wrapper script
- **Best for**: Cost-sensitive scenarios, local execution
- **Limitations**: Complex setup, performance issues
- **Status**: ⚠️ Working but slow

### Playwright (Stealth)
- **Speed**: ~2-4s with stealth features
- **Setup**: Python package + browser binaries
- **Best for**: Anti-detection, cross-browser testing  
- **Limitations**: Not AI-optimized, requires scripting
- **Status**: 🤔 Available but untested standalone

## Key Technical Findings

### CoinGlass Anti-Bot Detection
- Browserbase (cloud) → ✅ Full HTML captured
- Agent Browser (headless) → ❌ 404 blocked
- GSD Browser (local) → ❌ 404 blocked  
- Conclusion: Cloud infrastructure bypasses CDN filtering

### Data Loading Patterns  
- Initial HTML shows `$--` placeholders
- Real values loaded via client-side JavaScript/APIs
- Scraping requires either:
  1. Wait for JS completion (slower)
  2. Direct API access (preferred)

## Implementation

Created [[Browser Automation Skill]] with smart tool selection:
```python
def select_tool(task_type, instruction):
    if "simple fetch" in instruction: return "browserbase"
    if "complex workflow" in instruction: return "agent-browser" 
    if "stealth" in instruction: return "playwright"
    return "browserbase"  # default
```

## Cross-References
- [[Browserbase API]] - Cloud browser service details
- [[Agent Browser Guide]] - AI-native automation
- [[Trading Data Sources]] - CoinGlass alternatives
- [[Web Scraping Anti-Detection]] - Stealth techniques

## Lessons Learned
1. **Tool sprawl is real** - 4 overlapping tools before unification
2. **Cloud beats local** for bot detection bypass
3. **AI-native tools** reduce scripting overhead
4. **Speed matters** for trading applications (0.9s vs 6.3s)
5. **APIs > scraping** for reliable data extraction