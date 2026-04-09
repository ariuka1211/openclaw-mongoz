# BrowserOS MCP — Advanced Patterns

## Authentication Flows

For sites requiring login:
1. `new_page` → `navigate_page` to login URL
2. `take_snapshot` → `fill` credentials → `click` submit
3. Wait for redirect (`wait_for_selector` on post-login element)
4. Navigate to target page — session cookies persist

For OAuth flows, handle each redirect step with `take_snapshot` to find the right buttons/fields.

## Infinite Scroll / Lazy-Loading

```
loop:
  evaluate_script("window.scrollTo(0, document.body.scrollHeight)")
  wait_for_selector (new content selector)
  get_page_content (or evaluate_script to extract new items)
  check if end reached via evaluate_script
```

Set a max iteration count to avoid infinite loops.

## Iframe Handling

`take_snapshot` may not expose iframe internals. Use `evaluate_script` to access iframe content:
```javascript
document.querySelector('iframe').contentDocument.querySelector('#target').textContent
```

## Data Extraction Pipelines

For structured data from multiple pages:
1. Use `new_hidden_page` for each URL
2. Extract via `evaluate_script` returning JSON
3. Parse the JSON result in the agent
4. `close_page` when done with each

Batch similar pages in hidden pages to avoid tab clutter.

## Handling Popups & Modals

After navigation, `take_snapshot` to detect overlays. `click` the close/dismiss button, then re-snapshot before proceeding.

## JavaScript Extraction Patterns

Return data directly from `evaluate_script`:
```javascript
// Table data
JSON.stringify(
  Array.from(document.querySelectorAll('table tbody tr')).map(row =>
    Array.from(row.cells).map(c => c.textContent.trim())
  )
)

// Specific elements
JSON.stringify(
  Array.from(document.querySelectorAll('.price')).map(e => e.textContent.trim())
)
```

## Error Recovery

- Page crashed → `close_page` → `new_page` → retry
- Element not found → `take_snapshot` again (DOM may have changed)
- Navigation timeout → `reload_page` or navigate to a simpler URL first
- Stale element ID → always re-snapshot before interaction
