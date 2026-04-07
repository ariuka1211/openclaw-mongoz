# My-Brain-Is-Full-Crew

**Source:** https://github.com/gnekt/My-Brain-Is-Full-Crew
**Author:** PhD researcher (anonymous)
**Date:** 2026-04
**Type:** GitHub Repository / Claude Code Agents + Skills

## Summary

A full crew of 8 AI agents + 13 specialized skills that manage an Obsidian vault. Built by a PhD whose memory was failing, diet was wrecked, and anxiety had its own agenda.

> "Most AI + Obsidian tools are built for people who already have their life together and want to optimize. This one is for people who are drowning and need a lifeline."

**The tagline:**
> "I need to get organized, but I'm too exhausted to get organized"

## The Crew

| Agent | What it does |
|---|---|
| **Architect** | Vault structure & setup, creates folder structures, sets rules |
| **Scribe** | Takes messy brain dumps → clean Obsidian notes with links and deadlines |
| **Sorter** | Empties inbox every evening, routes notes to correct places |
| **Seeker** | Finds anything in vault, synthesizes answers with citations |
| **Connector** | Knowledge graph - discovers hidden links between notes |
| **Librarian** | Weekly health checks, deduplication, broken link repair |
| **Transcriber** | Meeting recordings → structured notes |
| **Postman** | Bridges Gmail/Hey.com + Google Calendar with vault |

## Key Design Decisions

- **Chat IS the interface** - no manual file management, no drag-and-drop
- **Any language** - works in Italian, French, German, Spanish, Japanese, etc.
- **Agents coordinate via dispatcher** - when one agent detects work for another, it chains them
- **Custom agents** - describe your problem, Architect builds the agent
- **Conservative by default** - agents never delete, always archive, ask before big decisions
- **PARA + Zettelkasten** structure with daily notes, MOCs, and Maps of Content

## How It Differs From Karpathy's Pattern

Karpathy's pattern is raw→wiki→schema, focused on knowledge accumulation. The Crew adds:
- **Life management** (email, calendar, meetings)
- **Daily journaling and note capture**
- **Inbox triage** (notes don't pile up)
- **Inter-agent coordination** (agents chain when they detect related work)
- **Focus on overwhelmed users** not researchers

## Custom Agent Examples
- "I keep starting side projects and abandoning them" → project-pulse
- "I keep buying the same thing because I forget" → home-inventory
- "I only spend 300 euros/month on groceries" → budget-tracker
- "My partner says I dress like I pick clothes with my eyes closed" → wardrobe-coach
