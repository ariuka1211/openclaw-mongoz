# Session Handoff — 2026-04-09 (afternoon wrap-up)

## Session Topic: VPS Cleanup Round 2 + Browser Skill Rewrite + Research

### What was done

**VPS Cleanup (continued from morning)**
- Removed: Docker CE + containerd + plugins (~364 MB)
- Removed: PyTorch torch (~1.2 GB, not used by any project)
- Removed: Golang 1.22 (~340 MB, browser-rod was the only Go code and was deleted)
- Removed: LLVM 18 + 20, Clang 18, bpftrace (~340 MB, eBPF tracing + GPU drivers — unused)
- Removed: WebKitGTK + Yelp + Evolution desktop libs (~89 MB)
- Removed: Pocketsphinx (~70 MB), CMake (~36 MB)
- Removed: archive/autopilot-trader/bot/venv (~156 MB)
- Removed: browser-rod Go tool + gsd-browser binary (replaced by BrowserOS)
- Kept: guacd (Guacamole proxy), ibus, OpenJDK 21 (all needed for GUI access via Guacamole)
- Total freed this session: ~2.8 GB | Disk: 30 GB → 26 GB (31% → 27%)
- Note: Whisper broke (needed torch) — reinstalled CPU-only torch (~190 MB) + ffmpeg

**Subagent model update**
- Changed from `kilocode/kilo-auto/free` → `modal/zai-org/GLM-5.1-FP8` (same as main)

**Browser-automation skill rewrite**
- Deleted all stale files: scripts/browser-automation.py, configs/browser-automation.json, references/agent-browser-advanced.md, references/browserbase-api.md, tools/, examples/
- Rewrote SKILL.md (~127 lines) centered on BrowserOS MCP as primary tool
- Added references/browseros-patterns.md (advanced patterns: auth flows, infinite scroll, iframes, etc.)
- Verified: 0 dead tool references, valid frontmatter, all old files deleted

**New project files created**
- `projects/advisor-executor-architecture/plan.md` — full plan for advisor/executor split with tool-level enforcement
- `projects/useful-tools.md` — curated research findings: Camofox, Browser Use, TLS fingerprinting, Advisor-Executor pattern, GSD, Clicky, Dialagram

### Research / Links Saved
- Anthropic advisor strategy: Opus as advisor + Sonnet/Haiku as executor
- Browser Use (browser-use.com): Python SDK + cloud for AI browser agents with stealth/CAPTCHA
- Camofox: Firefox fork with C++-level TLS fingerprint spoofing (for anti-bot sites)
- TLS fingerprinting concept: ClientHello = #1 way bots are detected
- Qwen 3.6 Plus: was free on OpenRouter (qwen/qwen3.6-plus-04-02:free) — now DEPRECATED, paid only
- Dialagram/Nexum Router: free OpenAI-compatible proxy for Qwen 3.5/GLM-5/DeepSeek at dialagram.me/router
- GSD (Get Shit Done): spec-driven dev system for AI coding agents, solves context rot
- Clicky: macOS AI tutor menu bar app, points cursor at UI elements, uses Claude+AssemblyAI+ElevenLabs

### Current State
- Disk: 26 GB / 96 GB (27%)
- BrowserOS: running, systemd service, MCP active
- Whisper: working (CPU torch + ffmpeg installed)
- Subagents: now GLM-5.1
- Google sign-in: still pending (needs Guacamole GUI)

### Pending
- Google sign-in via Guacamole (start tomcat10 when ready for GUI)
- Implement advisor-executor architecture (plan saved, do later)
- Consider adding swap (no swap configured, OOM risk)
- Git history cleanup with BFG (deferred)
