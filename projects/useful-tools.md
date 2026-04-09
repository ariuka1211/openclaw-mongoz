# Useful Tools & Findings

Curated list of tools, libraries, and techniques worth remembering. Not everything is installed — just tracked for when we need them.

---

## 🔒 Stealth Browsing & Anti-Bot

### Camofox
- **What:** Firefox fork with C++-level fingerprint spoofing
- **Why it matters:** Patches TLS ClientHello, navigator properties, WebGL, AudioContext, screen geometry, WebRTC BEFORE JS on the page can read them. Python/JS stealth libraries patch AFTER — anti-bot systems detect this.
- **Used by:** Hermes Agent (Nous Research), scraping community
- **When we need it:** Scraping any site behind Cloudflare/Akamai. Our BrowserOS (vanilla Chromium) gets flagged instantly on protected sites.
- **Status:** Not installed. Would replace or complement BrowserOS for protected targets.
- **Source:** @leftcurvedev_ on Twitter, 2026-04-09

### Browser Use
- **What:** Python SDK + cloud API for AI browser agents. Has its own LLM (bu-30b) trained for browser tasks.
- **Why it matters:** Cloud offers anti-detect browsers, CAPTCHA solving, proxy rotation, 195+ country proxies. 1000+ integrations (Gmail, Slack, Notion). Skill APIs turn websites into reusable endpoints.
- **When we need it:** Mass-scale parallel scraping, unknown sites with CAPTCHAs, production-grade automation at scale.
- **Cost:** Open-source SDK is free. Cloud/API has token costs ($0.20/$2 per 1M tokens). Custom model `bu-30b`.
- **vs BrowserOS:** Browser Use = smart intern who knows how to browse (LLM decides actions). BrowserOS = browser on our desk (we decide actions). BrowserOS wins for cost/latency/control. Browser Use wins for stealth/scale/CAPTCHA.
- **GitHub:** https://github.com/browser-use/browser-use
- **Status:** Not installed. Worth considering for production scraping.
- **Source:** https://browser-use.com, 2026-04-09

### TLS Fingerprinting (concept)
- **What:** Anti-bot systems (Cloudflare, Akamai) read the TLS ClientHello — the first message your browser sends during handshake. It reveals TLS version, cipher suites, elliptic curves, extensions order, GREASE values.
- **Why it matters:** This is the #1 way bots get detected. A wrong ClientHello = instant flag, regardless of how good your stealth JS is. Cloudflare protects 20%+ of all websites.
- **Key insight:** Proper TLS fingerprint spoofing requires low-level control (C++ or Rust). Python/JS can't do it — they leave obvious artifacts.
- **Source:** @leftcurvedev_ on Twitter, 2026-04-09

---

## 🧠 Agent Architecture

### Advisor-Executor Pattern (Anthropic)
- **What:** Pair a smart model (Opus) as advisor with a cheap/fast model (Sonnet/Haiku) as executor. Advisor plans and reviews, executor writes and runs.
- **Why it matters:** Near-Opus intelligence at a fraction of the cost. 80% of agent work is mechanical — only 20% needs frontier intelligence.
- **Our take:** We're 70% there (main session spawns subagents). Missing: (1) deliberately cheaper executor model, (2) structured executor prompts, (3) tool-level split so advisor literally can't write.
- **Full plan:** `projects/advisor-executor-architecture/plan.md`
- **Source:** @claudeai on Twitter, 2026-04-09

### The Drift Problem
- **What:** In multi-turn conversations, the advisor model inevitably starts doing executor work directly — editing files, running commands — instead of delegating.
- **Why it happens:** (1) Same tools available to both roles, (2) context compaction kills the role, (3) feels faster to just do it, (4) no feedback loop catches drift.
- **The fix:** Structural enforcement, not prompts. Take write tools away from the advisor. Read-only + spawn is all it needs.
- **Source:** Our own experience + Anthropic's advisor pattern discussion, 2026-04-09

---

## 🛠️ Our Current Stack (for reference)

| Component | What | Status |
|---|---|---|
| Browser | BrowserOS (Chromium) | Running, systemd service |
| Browser MCP | `browseros__*` tools (53+) | Active in OpenClaw |
| Stealth | None | ❌ No anti-detect, no CAPTCHA solving |
| Proxies | None | ❌ Direct VPS IP |
| Agent | OpenClaw + GLM-5.1 | Running |
| Subagents | GLM-5.1 (was kilocode/kilo-auto/free) | Updated 2026-04-09 |
| Fallbacks | Sonnet 4.6, Gemma 4 | Configured |

---

## 🖥️ AI-Powered Desktop & Tutoring

### Clicky
- **What:** macOS menu bar app — AI tutor that watches your screen, talks to you, and points at UI elements. Like having a teacher sitting next to you.
- **How it works:** Push-to-talk streams audio → AssemblyAI transcription → sends transcript + screenshot to Claude → Claude responds via ElevenLabs TTS. Claude embeds `[POINT:x,y:label:screenN]` tags that make a blue cursor fly to specific UI elements on screen.
- **Tutor mode:** Instead of waiting for you to talk, it watches what you're doing and proactively guides you — points at buttons, explains what things do, suggests next steps. Uses idle detection (pauses after your actions) to trigger observations.
- **Architecture:** Swift macOS app (menu bar, no dock icon) + Cloudflare Worker proxy for API keys. Three API calls: Anthropic (Claude), AssemblyAI (speech-to-text), ElevenLabs (text-to-speech). All proxied through the Worker so keys never ship in the app binary.
- **When useful:** Learning new apps/tools, onboarding, teaching someone remotely, building interactive tutorials.
- **Limitations:** macOS only. Requires paid API keys (Anthropic, AssemblyAI, ElevenLabs). Not relevant to our VPS/headless setup.
- **GitHub:** https://github.com/danpeg/clicky
- **Source:** @FarzaTV on Twitter, 2026-04-09

### GSD (Get Shit Done)
- **What:** Spec-driven development system for AI coding agents (Claude Code, OpenCode, Codex, Cursor, etc.). Meta-prompting + context engineering layer.
- **Core problem it solves:** Context rot — AI coding quality degrades as the conversation gets longer. GSD breaks work into atomic tasks that each run in a fresh 200K context window with zero accumulated garbage.
- **Workflow:** `/gsd-new-project` → `/gsd-discuss-phase` → `/gsd-plan-phase` → `/gsd-execute-phase` → `/gsd-verify-work` → `/gsd-ship` — loop until milestone complete.
- **Key features:** Wave execution (parallel independent plans, sequential dependent ones), quality gates (schema drift, security, scope reduction detection), fresh context per task, atomic commits, STATE.md for resumability.
- **Where it shines:** Solo devs building full products, big features spanning many files, long multi-milestone projects.
- **Where it's overkill:** Quick bug fixes, small changes, active pair-programming.
- **Relevance to us:** Does a lot of what we built manually in AGENTS.md (structured planning, subagent spawning, verification checklists, atomic commits). More formalized — handles full project lifecycle. Fresh-context-per-task is similar to our advisor-executor idea.
- **GitHub:** https://github.com/gsd-build/get-shit-done
- **Source:** 2026-04-09

### Dialagram / Nexum Router
- **What:** Free OpenAI-compatible API endpoint for Qwen models (3.5 Plus, GLM-5, DeepSeek). Sign up → generate access token → use `https://www.dialagram.me/router/v1` as base URL.
- **How it works:** Don't know the backend. Provides managed Qwen accounts with an OpenAI-compatible router. Free tier available.
- **Could work with OpenClaw:** Yes — add as custom provider with the router base URL and access token.
- **Caveat:** I don't know how their free tier is funded or how long it lasts. Third-party proxy, not Qwen's official API.
- **URL:** https://dialagram.me/router
- **Source:** @masfiq018 on Twitter, 2026-04-09

---

*Last updated: 2026-04-09*
