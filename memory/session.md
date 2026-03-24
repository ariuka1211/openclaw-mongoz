# Session Handoff — 2026-03-24 15:59 MDT

## Session Summary
- Investigated how memory search works — QMD semantic search backend
- QMD = local vector engine using embeddinggemma-300m, stores in SQLite DB
- Found `memory_search` was returning empty results in this Telegram group
- Root cause: **group scope restriction** — OpenClaw blocks memory_search in group chats by design (security feature). Not a QMD issue.
- QMD DB healthy: 84 docs indexed, vectors present
- Cleaned stale entries from agent DB (removed ~86 phantom root-level entries)
- Ran `qmd update` on CLI — re-indexed to 39 docs
- Restarted gateway (with John's approval) to reload QMD state

## Key Findings
- `memory_search` works in DMs and CLI, blocked in group chats
- QMD lowercases all filenames internally — harmless for search
- Two separate QMD databases: CLI (`/root/.cache/qmd/`) and agent (`/root/.openclaw/agents/main/qmd/`)
- Group scope denial logged as: `qmd search denied by scope (channel=telegram, chatType=group)`

## Decision
- Leave memory_search scope restriction as-is (John approved)
- No config changes needed

## Open Items
- None

## Trading Status
- No trading activity this session
