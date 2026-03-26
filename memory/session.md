# Session Handoff — 2026-03-26

## Active: AI-decisions model switch to OpenRouter xiaomi/mimo-v2-pro

### What happened
- Switched AI-decisions from Kilo (minimax/minimax-m2.5) → OpenRouter (xiaomi/mimo-v2-pro)
- Built provider-based config system in config.json (easy switching)
- Hit 402 errors on direct OpenRouter calls — was HTTP-Referer header issue
- Fixed: added `HTTP-Referer: https://openclaw.ai` header in llm_client.py
- Model works free on OpenRouter with proper headers (0 credits, no 402)
- max_tokens bumped to 15000 (xiaomi is a thinking model, burns tokens on reasoning)

### Current state
- **AI-decisions**: running on OpenRouter with xiaomi/mimo-v2-pro, max_tokens=15000
- **Config**: `"provider": "openrouter"` in config.json
- **Provider switching**: change one line in config.json between "openrouter" and "kilo"
- **Cost**: free on OpenRouter with proper headers

### Files changed
- `ai-decisions/config.json` — provider system + max_tokens=15000
- `ai-decisions/llm_client.py` — provider resolution + HTTP-Referer header fix

### Not committed yet
- Branch: none (changes on working tree)
