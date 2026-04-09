# Session Handoff — 2026-04-09 (afternoon)

## Session Topic: AGENTS.md Cleanup + BrowserOS Setup + System Cleanup

### What was done

**AGENTS.md / MEMORY.md cleanup**
- Removed Grid Bot, Watchdog from MEMORY.md (dead references)
- Moved Second Brain auto-ingest rule → AGENTS.md (new section)
- Removed stale session flow refs: `session_memory_auto.py`, `track_exchange()`, `wrap_up_session()`
- Replaced with `memory_search` for During Session
- Rewrote TOOLS: Tavily (primary) → DuckDuckGo → Exa
- Clarified `__slots__` lesson

**API Keys added to `.env`**
- `TAVILY_API_KEY` — verified working
- `EXA_API_KEY` — verified working

**BrowserOS setup**
- Confirmed BrowserOS at `/usr/bin/browseros` (v146.0.7821.31)
- Created systemd service: `browseros.service` — enabled, auto-starts on boot
- Profile: `/root/.config/browser-os` (persistent)
- CDP: `ws://127.0.0.1:9101`
- MCP server: `http://127.0.0.1:9201/mcp`
- Added MCP to `openclaw.json` under `"mcp"."servers"."browseros"` (streamable-http)
- Gateway restarted, MCP active ✅
- BrowserOS MCP tools now available (53 browser tools, 40+ app integrations)
- Attempted Google sign-in — blocked by passkey on account, needs GUI (Guacamole)

**Browser cleanup**
- Uninstalled `google-chrome-stable`, `chromium-browser`, Playwright Chromium
- All browser needs now handled by BrowserOS

**System cleanup**
- Stopped/disabled `tomcat10` (Guacamole) — `systemctl start tomcat10` to re-enable
- Deleted `autopilot-trader-v2/.venv` (239 MB)
- Cleaned Go module cache (916 MB → 20 KB)
- Disk: 30 GB / 96 GB (31%)

**Other**
- Created `REMINDERS.md` with Go module cache note
- Switched session model from Sonnet fallback back to GLM-5.1
- Added `.env` with Tavily + Exa keys

### Current State
- BrowserOS running as systemd service, MCP active in OpenClaw
- Google not signed in (needs Guacamole + passkey auth — do later)
- Guacamole stopped/disabled (start when needed for GUI)
- Model: `modal/zai-org/GLM-5.1-FP8` (primary), fallbacks: Sonnet 4.6, Gemma 4

### Pending (John to do later)
- Google sign-in via Guacamole GUI (BrowserOS → passkey auth → stay signed in)
- Consider adding swap (no swap configured, OOM risk)
- Git history cleanup (BFG for old PNGs — deferred)
