# Session Handoff — 2026-04-09 (4:39 PM MDT)

## Session Topic: Model Fix + BrowserOS Repair + Tweet Fetch

### What was done

**GLM-5.1 Model Fix (ROOT CAUSE)**
- Found the Modal API key `ak-mDirc91XkBXKyWjCtMmt1k:as-YfYEZt4TXbWmZPpyg1YQQ0` was **invalid** (401)
- Replaced with `modalresearch_EH6ohwJx4bkrryhbyiiaWbf3XSMSUqIEOM9fkZrDr9U` in `models.json`
- Confirmed working: 1.3s response time via direct curl test
- This was why every /new session fell back to Sonnet — GLM timed out due to dead token
- Increased timeout from 300s → 600s in openclaw.json
- Sonnet restored as fallback (John explicitly wanted it)
- Removed Sonnet from fallbacks briefly, then added it back per John's request

**BrowserOS Repair**
- Original binary at `/usr/bin/browseros` was gone (likely removed during cleanup)
- Downloaded fresh BrowserOS AppImage (273.6 MB) via `browseros-cli install`
- Extracted to `/opt/browseros/squashfs-root/` (no FUSE on VPS)
- Added Xvfb as virtual display (headless VPS needs it)
- Fixed systemd service to point to `/opt/browseros/squashfs-root/opt/browseros/browseros`
- BrowserOS now running on port 9200 (MCP), 9101 (CDP)
- Updated openclaw.json: MCP URL changed from 9201 → 9200
- **Verified working**: sent Coinglass liquidation heatmap screenshot to John

**Tweet Fetch (gittrend0x)**
- fxtwitter/vxtwitter both failed on the X link
- Used `agent-browser` CLI as a hack to fetch the tweet content
- Later confirmed BrowserOS MCP tools work natively
- 5 trending GitHub projects: camel-ai/owl, letta-ai/agent-file, simstudioai/sim, iflytek/astron-agent, mcp-use/mcp-use

**sessions.json Cleanup**
- Found sticky model override in sessions.json (`model: claude-sonnet-4.6`) surviving /new
- Removed the override fields; the root cause was the dead API key causing timeouts → fallback

### Current State
- **Model**: GLM-5.1 (working, fast, proper API key)
- **Fallback**: Sonnet 4.6 → Gemma 4 31B
- **BrowserOS**: Running, systemd service, MCP on port 9200, CDP on 9101
- **Disk**: ~27% (BrowserOS AppImage added ~274 MB)
- **Pending**: Google sign-in via Guacamole (not urgent)

### Key Files Changed
- `/root/.openclaw/agents/main/agent/models.json` — fixed Modal API key
- `/root/.openclaw/openclaw.json` — MCP port 9201→9200, timeout 600s, fallbacks updated
- `/etc/systemd/system/browseros.service` — new service file for extracted AppImage
- `/opt/browseros/` — extracted BrowserOS binary

### ⚠️ Known Issues
- BrowserOS runs on port 9200 now (was 9201 before). If anything references 9201, it'll fail.
- The AppImage at `/usr/bin/browseros` is the raw AppImage (won't run without FUSE). The actual binary is at `/opt/browseros/squashfs-root/opt/browseros/browseros`
- `agent-browser` npm package is installed globally — it's a SEPARATE tool from BrowserOS, don't confuse them
