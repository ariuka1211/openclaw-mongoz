---
name: browser-automation
description: Browser automation via BrowserOS MCP. Navigate, interact, scrape, screenshot, and execute JavaScript on any website using native OpenClaw tools. No external services or API keys needed.
---

# Browser Automation with BrowserOS MCP

BrowserOS MCP provides 53+ native browser tools directly in OpenClaw. All tools are prefixed `browseros__` and require no installation, API keys, or scripts.

## Tool Hierarchy

1. **BrowserOS MCP** — primary tool for all browser automation (navigation, interaction, screenshots, forms, data extraction, JavaScript execution)
2. **web_fetch** — fallback for simple static page content extraction (no interaction needed, lighter weight)
3. **web_search** — find URLs before navigating to them

## Core Workflow

```
1. browseros__list_pages          → find existing pages
2. browseros__new_page            → create a page (or browseros__new_hidden_page for background)
3. browseros__navigate_page       → go to URL
4. browseros__take_snapshot       → see interactive elements with IDs
5. browseros__click               → click an element
   browseros__fill                → fill an input field
   browseros__press_key            → press a key
6. browseros__get_page_content    → extract clean markdown
7. browseros__take_screenshot    → capture visual state
8. browseros__evaluate_script    → run custom JavaScript
```

Always `take_snapshot` before interacting — it returns element IDs needed by `click` and `fill`.

## Setup (Already Done)

- BrowserOS runs as a systemd service on this VPS (CDP: `ws://127.0.0.1:9101`)
- MCP server configured in `openclaw.json` under `mcp.servers.browseros`
- Persistent Chromium profile at `/root/.config/browser-os`
- All `browseros__*` tools available natively

## Common Use Cases

### Web Scraping (Dynamic/JS-heavy sites)
```
browseros__new_page → navigate_page → wait for content → get_page_content
```
Use `evaluate_script` if content loads via AJAX and needs a wait.

### Form Filling & Login Flows
```
navigate_page → take_snapshot → fill(username) → fill(password) → click(submit)
```

### Screenshot Capture / Monitoring
```
navigate_page → take_screenshot
```
Use `take_screenshot` with element selectors for partial captures.

### Data Extraction (Trading/Finance Sites)
```
navigate_page → take_snapshot → click(tabs/filters) → get_page_content → parse markdown
```
For CoinGlass, TradingView, etc. — interact with filters/tabs first, then extract.

### Multi-Tab Research Sessions
```
browseros__new_page × N → navigate each → group_tabs → switch between pages
```
Use `browseros__group_tabs` to organize. `browseros__list_pages` to see all open tabs.

### File Downloads
```
navigate_page → click(download link) → check downloads via evaluate_script or filesystem
```

## Hidden Pages

`browseros__new_hidden_page` creates a page without a visible tab. Use for:
- Background scraping without disturbing the user
- Parallel data collection
- Headless-style automation

## Key Tools Reference

| Tool | Purpose |
|------|---------|
| `browseros__list_pages` | List all open pages |
| `browseros__new_page` | Open a new visible page |
| `browseros__new_hidden_page` | Open a background page |
| `browseros__navigate_page` | Navigate to URL |
| `browseros__take_snapshot` | Get interactive elements with IDs |
| `browseros__click` | Click an element by ID |
| `browseros__fill` | Fill an input field |
| `browseros__press_key` | Press a keyboard key |
| `browseros__get_page_content` | Extract page as clean markdown |
| `browseros__take_screenshot` | Capture screenshot (full or element) |
| `browseros__evaluate_script` | Run JavaScript in page context |
| `browseros__go_back` | Navigate back |
| `browseros__go_forward` | Navigate forward |
| `browseros__reload_page` | Reload current page |
| `browseros__close_page` | Close a page |
| `browseros__group_tabs` | Group tabs together |
| `browseros__scroll_page` | Scroll the page |
| `browseros__select_option` | Select dropdown option |
| `browseros__hover` | Hover over element |
| `browseros__drag` | Drag element |
| `browseros__upload_file` | Upload a file |
| `browseros__wait_for_selector` | Wait for element to appear |

## Tips

- Call `take_snapshot` after every navigation or significant interaction — element IDs can change.
- Use `wait_for_selector` before interacting with dynamically loaded content.
- `get_page_content` returns markdown — often cleaner than parsing HTML via `evaluate_script`.
- For complex extractions, combine `evaluate_script` with `get_page_content`.
- Hidden pages share the same browser profile — cookies and sessions persist across visible and hidden pages.
- If a page is unresponsive, `close_page` and `new_page` rather than debugging the stuck state.

## When to Use web_fetch Instead

- Static content with no JavaScript rendering needed
- Quick URL content extraction without interaction
- When browser overhead isn't justified

## Advanced Patterns

See `references/browseros-patterns.md` for advanced techniques including handling auth flows, infinite scroll, iframes, and multi-step extraction pipelines.
