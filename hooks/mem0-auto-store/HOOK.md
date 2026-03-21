---
name: mem0-auto-store
description: "Auto-stores session context to MEM0 semantic memory when /new or /reset is issued"
homepage: https://docs.openclaw.ai/automation/hooks#mem0-auto-store
metadata:
  {
    "openclaw":
      {
        "emoji": "🧠",
        "events": ["command:new", "command:reset"],
        "requires": { "env": ["MEM0_API_KEY"] },
      },
  }
---

# MEM0 Auto-Store Hook

Automatically stores conversation context to MEM0 when a session is reset via `/new` or `/reset`.

## What It Does

1. Reads the last N user/assistant messages from the session transcript
2. POSTs them to the MEM0 Platform API for automatic fact extraction
3. MEM0 extracts and stores key facts/preferences as semantic memories

## Configuration

| Option     | Type   | Default | Description                                          |
| ---------- | ------ | ------- | ---------------------------------------------------- |
| `messages` | number | 15      | Number of recent messages to send to MEM0            |

Requires `MEM0_API_KEY` environment variable or `plugins.entries.mem0.config.apiKey`.
