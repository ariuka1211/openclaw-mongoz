# LEARNINGS.md — Don't Repeat These 🔴

**Before acting:** check Pre-Action Protocol in AGENTS.md.

## 🔴 Active

| ID | Mistake | Prevention |
|---|---|---|
| M001 | Deploy without understanding build system | After build: verify runtime reads correct files, test page before declaring done |
| M002 | Restart service without verifying it works | After ANY restart: warn John first, curl localhost to verify |
| M003 | Build replacements for existing systems | Check what exists first. Extend, don't rebuild |
| M010 | Edit openclaw.json without reading docs | Read docs before touching config |
| M020 | Assume root cause without investigation | Check simplest explanation first (does the file even exist?) |
| M031 | Try random free models | Check models.allowlist before using any model |
| M032 | Restart gateway on ambiguous permission | Require explicit "yes" before restart. Ambiguous = clarification question |
| M061 | Spawn subagent without checking existing code | `find ... | grep -i <keyword>` first. Include file paths in spawn prompt |
| L002 | Doing code work in main session | NEVER edit/debug code in main session. Spawn Mzinho immediately |

## 🟡 Watch

M051 (install without audit), M050 (auto-scripts without approval), M011 (wrong model prefix), M021 (correlation≠causation), M030 (batch-spawn), M040 (thinking loop — burn tokens on repeated reasoning)
