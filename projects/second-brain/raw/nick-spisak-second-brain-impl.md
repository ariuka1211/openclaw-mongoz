# Second Brain Implementation by Nick Spisak

**Source URL:** https://github.com/NicholasSpisak/second-brain  
**Author:** Nick Spisak (@NickSpisak_)  
**Date:** 2026-04-04  
**Type:** GitHub Repository / Agent Skills

## Content

Complete implementation of Karpathy's LLM Wiki pattern. Turnkey project you can install with `npx skills add NicholasSpisak/second-brain`.

### What It Ships

Four skills installable via npm/Agent Skills:
1. `/second-brain` — Setup wizard (guided, 5 steps)
2. `/second-brain-ingest` — Process raw sources into wiki pages  
3. `/second-brain-query` — Ask questions against the wiki
4. `/second-brain-lint` — Health-check the wiki

### Directory Structure

```
raw/ → raw sources (append-only inbox)
wiki/ → LLM-maintained wiki
  sources/ → one summary per source
  entities/ → people, orgs, tools
  concepts/ → ideas, frameworks, theories
  synthesis/ → comparisons, analyses
  index.md → master catalog
  log.md → chronological record
output/ → reports, artifacts
CLAUDE.md → agent instructions
```

### Optional Tooling
- **summarize** — CLI to summarize links/files/media
- **qmd** — local markdown search engine  
- **agent-browser** — browser automation for web research

### Philosophy

> "The LLM is the librarian. You're the curator."

### Stats
1,361 likes, 142 retweets on announcement
