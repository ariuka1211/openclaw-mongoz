# Session Handoff — 2026-03-24

## What Happened
- Major memory system overhaul — removed all custom scripts and pipelines
- Deleted: memory-session-extract.sh, memory-nightly-cleanup.sh, memory-archive-dailies.sh, memory-search.sh, memory-llm.sh, obs-log.sh, daily-summary.sh, backup-memory.sh, learning-graduate.sh
- Deleted: LEARNINGS.md (merged critical lessons into LESSONS.md, then deleted LESSONS.md too)
- Deleted: SOUL.md, IDENTITY.md, TOOLS.md, USER.md, HEARTBEAT.md (stubbed, OpenClaw recreates them)
- Deleted: PROJECTS/ folder — moved docs to projects/autopilot-trader/docs/
- Deleted: freddy.db (0 bytes), reference/SOUL-beliefs.md
- Created: LESSONS.md → later deleted, lessons moved to AGENTS.md (universal) + trading-lessons.md (trading-specific)
- Created: projects/autopilot-trader/docs/trading-lessons.md

## New System
- **AGENTS.md** — one file, everything: persona, session flow, tools, hard rules, subagent+git workflow, key lessons
- **MEMORY.md** — lean long-term knowledge
- **memory/session.md** — handoff file, overwritten each wrap up
- **memory/YYYY-MM-DD.md** — daily record, appended each wrap up
- **projects/autopilot-trader/docs/** — all trading docs

## AGENTS.md Evolution
- Session flow: Start → do one thing → 🔴 WRAP UP (overwrite session.md + append daily)
- "Wrap up" is the trigger word — does two things automatically
- Hard rules: 7 rules, 🚨 formatting, merged pre-task gate into rule #1
- Subagent + Git: merged into one flow "Subagent → Commit → Ship"
- Tools: Tavily/DDG/Exa, defuddle, fxtwitter/vxtwitter for X links

## State
- All cleanup done
- AGENTS.md final at ~80 lines
- MEMORY.md at 25 lines
- Crontab: just lighter scanner
- Scripts remaining: git/, monitoring/, search/
