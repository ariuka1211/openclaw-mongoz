# MEMORY.md — Techno4k 🛠️

**John** — Building a crypto perps trading bot on dedicated VPS (srv1435474). Wants lean systems, no bloat.

**My Role** — Infrastructure agent. Maaraa (main agent) sends me infra tasks via `sessions_spawn`. I handle server ops, deployments, system administration. I do NOT wait for John to talk to me — Maaraa coordinates everything.

---

## Server

- **Hostname:** srv1435474
- **Specs:** 4 vCPU, 8GB RAM, 80GB disk, Linux x64
- **Timezone:** UTC
- **Gateway port:** 5705

### Disabled Services (2026-03-20 — permanent, won't come back on reboot)
- `surfsharkd2` — Surfshark VPN (system-level)
- `docker`, `containerd`, `docker.socket` — Docker (unused)
- `tor` — Tor daemon
- `ModemManager` — Modem manager (useless on VPS)
- `wpa_supplicant` — WiFi (useless on VPS)
- `snapd`, `snapd.socket`, `snapd.seeded` — Snap daemon
- `monarx-agent` — Security scanner (host-level, not ours)
- `mission-control` — Dashboard, spawned next-server (Surfshark dep)

**Kept:** OpenClaw gateway, watchdog daemon, AI trader, tailscaled, nginx, fail2ban, xray

### Disk Cleanup (2026-03-21 — freed 28GB)
- Removed nvidia/CUDA/PyTorch/torch/triton from `/usr/local/lib/python3.12/dist-packages/` (6.8GB — no GPU on VPS)
- Cleaned pip/uv/whisper/playwright/npm caches (~12GB total across /root and /home/john)
- Removed all snap packages (4.7GB — snapd disabled)
- Removed Surfshark leftovers: `/opt/Surfshark`, `/root/mission-control`, `/root/.surfshark`
- Removed unused dev tools: `.bun`, `.vscode-server`, `.cloakbrowser`, `.agent-browser`, `.node-llama-cpp`
- Emptied trash (2.4GB)
- **Disk went from 42% (40GB) → 13% (12GB)**

## Key Services

### OpenClaw Gateway
- Multi-agent system: `main` (Maaraa), `blitz` (Research), `system` (Techno4k), `coder` (Mzinho)
- LCM: `/root/.openclaw/lcm.db` — conversation summaries with FTS

### Watchdog Daemon
- Script: `/root/.openclaw/workspace/watchdog-daemon.sh`
- Monitors: gateway health, OpenRouter provider, log errors, sub-agents
- Alerts: Telegram bot → John's personal chat

### Cron Jobs
- Reflection: every 3 days at 03:00 UTC (trade pattern analysis)
- Bot health check: every 15 min (disabled, for 429 verification)

### Lighter Trading Bot — Python Copilot
- **Path:** `/root/.openclaw/workspace/executor/`
- **Stack:** Python, asyncio, aiohttp, Lighter SDK
- **Config:** `config.yml` — includes `price_call_delay: 5.0` for rate limiting
- **Run:** `cd /root/.openclaw/workspace/lighter-copilot && nohup python3 -u bot.py > bot.log 2>&1 &`
- **PID check:** `pgrep -f "python3.*bot.py"`
- **Log:** `/root/.openclaw/workspace/executor/bot.log`
- **AI mode:** Uses OpenRouter API for trade decisions
- **Key files:** `bot.py`, `reflection.py`, `signal_analyzer.py`, `db.py`, `context_builder.py`

### Lighter Trading Bot — TypeScript Scanner
- **Path:** `/root/.openclaw/workspace/signals/`
- **Stack:** TypeScript + Node.js
- **Scanner log:** `/root/.openclaw/workspace/signals/scanner.log`

### Removed VPN/Proxy Packages (2026-03-21)
- `surfshark` + `surfshark-vpn` — purged, all files/configs wiped
- `cloudflare-warp` — purged
- `xray` — killed process, removed binary + `/usr/local/etc/xray/`
- `openvpn` + `network-manager-openvpn` — purged
- `wireguard` + `wireguard-tools` + protonvpn config — purged
- All systemd services, init.d scripts, rc symlinks, apt repos cleaned
- **Kept:** Tailscale (legit networking) + nginx (web server)

### Disk Cleanup (2026-03-21)
- Freed **28GB**: nvidia/CUDA/PyTorch (6.8GB), pip caches (8GB), snaps (4.7GB), Surfshark leftovers (1.5GB), dev tools (2.7GB), trash (2.4GB)
- Disk: 40GB (42%) → 12GB (12%)

## Incidents

### 2026-03-20: System freeze + reboot
- Server rebooted ~16:21 UTC after ~9 days uptime. Likely OOM/gateway hang.
- Surfshark + bloat services came back after reboot because we only killed them before (didn't disable).
- Fixed by disabling all bloat services permanently via systemctl disable.
- Freed ~1GB RAM, load dropped from 2.22 → 0.58.

## SOP & Pre-Task Gate
- SOP-BIG-TASK-GATE.md lives in `/root/.openclaw/agents/system/agent/SOP-BIG-TASK-GATE.md`
- **Maaraa's SOUL.md is at `/root/.openclaw/workspace/SOUL.md`** — NOT in agents/main/agent/
- Pre-Task Gate with 9 objective triggers added to workspace SOUL.md
- Delegation rule: split tasks across multiple agents in parallel

## Code Review Log
- **opportunity-scanner.ts (2026-03-19):** Found 2 CRITICAL NaN issues — API data validation missing, funding rate NaN poisoning. Fixed by Mzinho.
- Key check: always verify `Number.isFinite()` on API numeric fields in any trading code.
- **bot.py (2026-03-20):** Rate limiting issues — 429 errors from Lighter API. Fixed with 5.0s delay between API calls.
- **bot.py (2026-03-20):** Telegram alert could hang forever without timeout. Fixed with `asyncio.wait_for`.

## Lighter API
- **Base URL:** `https://mainnet.zklighter.elliot.ai`
- `GET /api/v1/orderBookDetails` — markets with price, volume, OI, leverage
- `GET /api/v1/funding-rates` — hourly funding rates
- 165 markets total, ~101 liquid
- **Rate limits:** 60 req/min standard

## John's Preferences

- Lean systems, no bloat
- `trash` > `rm`
- No gateway restarts without permission
- Test before declaring done

## Roster (Multi-Agent System)
- **Maaraa (main):** Coordinator. I receive tasks from him via `sessions_spawn`.
- **Blitz (research):** Research specialist.
- **Techno4k (me):** Infrastructure, ops, code review.
- **Mzinho (coder):** Coding specialist.

## Git Workflow (2026-03-21)

**Branch + PR workflow — never push to main directly:**
1. Create branch before work: `git checkout -b <agent-id>/<short-description>`
2. Do work, commit with `[agent-id] description` format
3. Push branch: `git push origin <branch-name>`
4. Report branch name to Maaraa — do NOT create PR or merge
5. Use `./scripts/git-agent-commit.sh <agent-id> "message" <files>` for commits

**Branch naming:** `<agent-id>/<verb>-<description>` (e.g., `techno4k/deploy-scanner`)

**Safety rules:**
- Run `git status` and `git diff --cached` before committing
- Never commit runtime files (signals/*.json, *.log, *.db, *.jsonl)
- Never force push
- One logical change per commit
- If unsure about a file, don't commit it

---
_Last updated: 2026-03-21 04:41 UTC_
