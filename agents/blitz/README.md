# 🦊 Maaraa — Workspace

OpenClaw assistant workspace for John.

## Key Files
- `SOUL.md` — Maaraa's personality and boundaries
- `USER.md` — About John (preferences, context)
- `AGENTS.md` — Startup protocol, pre-action checks, rules
- `MEMORY.md` — Long-term memory (tagged sections: projects, prefs, facts, rules, decisions, lessons, patterns)
- `LEARNINGS.md` — Mistake patterns with auto-promotion tiers (Active → Watch → Retired)
- `TOOLS.md` — Environment-specific notes (search APIs, scripts)
- `HEARTBEAT.md` — Periodic tasks (empty = skip)

## Memory System
- **Signals DB** (`data/freddy.db`) — facts, preferences, patterns with confidence scores
- **Learnings DB** (`data/freddy.db`) — mistakes with auto-graduation
- **LCM** — automatic conversation summarization (lossless-claw plugin)
- **Scripts** (`scripts/`) — `memory-profile.sh`, `memory-surface.sh`, `memory-merge.sh`, `memory-extract.sh`, `learning-check.sh`, `learning-log.sh`, `learning-graduate.sh`, `learning-capture.sh`, `signal-decay.sh`

## Cron Jobs (system-level)
- Memory extraction: every 6 hours
- Signal decay: daily at 04:00 UTC
- Learning graduation: weekly Sunday 03:00 UTC
- Memory backup: every 6 hours
