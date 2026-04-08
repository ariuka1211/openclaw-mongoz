# Session Handoff — 2026-04-08

## What Happened Today — VPS GUI + BrowserOS Setup

### VPS Cleanup
- Cleaned npm cache, pip cache, Docker images — freed ~5GB
- Killed VSCode server, disabled dashboard
- RAM: 2.3GB used (down from baseline)

### GUI Installation
- Installed XFCE desktop
- Tried noVNC — Chrome extension conflict (Backpack wallet)
- Tried Guacamole — failed to start (Tomcat port conflict)
- Settled on: x11vnc + websockify on port 6080

### BrowserOS Installation
- Installed BrowserOS .deb package from https://github.com/browseros-ai/BrowserOS
- Runs headless (--headless --no-sandbox --disable-gpu)
- CDP on port 9100, MCP server on port 9200
- 19 processes, ~700MB RAM

### Control Method
- **Playwright via CDP** → BrowserOS on port 9100
- Speed: ~8 seconds per page load
- Works reliably
- MCP server broken (Klavis API dependency issue)

### Screenshots Sent
- CoinGlass liquidation heatmap
- Binance proof screenshot
- Issue: Telegram won't accept /tmp/ files, must use ~/.openclaw/workspace/

### Services Running
| Service | Port | Status |
|---------|------|--------|
| openclaw-gateway | - | Running |
| BrowserOS (headless) | 9100 CDP | Running |
| x11vnc | 5900 | Running |
| websockify/noVNC | 6080 | Running |

### Next Steps
- Use headless when automating
- Use noVNC (port 6080) for manual GUI access
- When John wants GUI: restart x11vnc + noVNC

### Key Lessons
- BrowserOS works great via Playwright → CDP
- Telegram: save screenshots to ~/.openclaw/workspace/ before sending
- Headless saves ~300MB RAM vs GUI mode
- noVNC works through browser, no client needed
