# Session Handoff — 2026-03-24 16:43 MDT

## What Happened
- QMD database audit and cleanup
- Fixed 11 case mismatches (AGENTS.md, SKILL.md, etc.) in both CLI and agent QMD
- Removed 3 garbage entries (2 venv files + deleted lighter-quota-research.md)
- Removed redundant collections: `memory-root-main`, `memory-alt-main`, `workspace` (from agent QMD)
- Agent QMD went from 84 → 40 docs across 2 collections (`custom-1-main`, `memory-dir-main`)
- Updated `agents/main/qmd/xdg-config/index.yml` — removed stale collections, added stubs to ignore list
- Discovered: OpenClaw gateway holds a lock on agent QMD DB, so raw SQL deletes don't persist while gateway runs

## Pending
- **QMD DB cleanup still needed:** 6 entries stuck in agent QMD (5 stubs + lighter-quota-research.md) because gateway lock prevents deletion. Next time gateway restarts, run:
  ```bash
  sqlite3 /root/.openclaw/agents/main/qmd/xdg-cache/qmd/index.sqlite "
  DELETE FROM documents WHERE path IN ('heartbeat.md','identity.md','soul.md','tools.md','user.md','projects/autopilot-trader/executor/lighter-quota-research.md');
  DELETE FROM content WHERE hash NOT IN (SELECT DISTINCT hash FROM documents);
  DELETE FROM content_vectors WHERE hash NOT IN (SELECT DISTINCT hash FROM documents);
  DELETE FROM documents_fts WHERE rowid NOT IN (SELECT id FROM documents);
  VACUUM;"
  ```
- Config is already updated — next re-index will exclude stubs automatically

## Notes
- CLI QMD (`/root/.cache/qmd/index.sqlite`) — 36 docs, clean, untouched
- Agent QMD is what powers `memory_search` in sessions
- Lesson learned: don't edit QMD DB with raw SQL while gateway is running — use subagents but they'll face the same lock issue
