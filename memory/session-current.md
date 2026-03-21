# Session Current — 2026-03-21

## QMD Audit & Cleanup
- Tested QMD — binary installed but index had 0 docs in main cache, 194 in workspace collection
- Data audit found ~56% trash: 102 backup duplicates, LCM remnants, venv files, stale paths
- Cleaned LCM remnants (memory-staging.md, lcm-monitor.log, lcm-cleanup-cron.md)
- Archived stale SESSION_STATE.md
- Moved backups from workspace to ~/.openclaw/backups/ — QMD has no exclude syntax
- QMD re-indexed: 194 → 92 clean docs, search working properly
- John decided: wrap-up stays manual, not automated
