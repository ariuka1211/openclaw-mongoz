# browser-rod Cheatsheet

Go-based CDP client for BrowserOS — lighter and faster than Playwright.

## Setup

```bash
cd /root/.openclaw/workspace/browser-rod
go run cmd/test/main.go
```

## Usage

```go
package main

import (
	"fmt"
	"browser-rod"
)

func main() {
	// Connect to BrowserOS page
	client, _ := browser.NewFromPage("ws://localhost:9100/devtools/page/<PAGE_ID>")
	defer client.Close()

	// Navigate
	client.Navigate("https://example.com")

	// Get HTML
	html, _ := client.HTML()

	// Screenshot
	img, _ := client.Screenshot(true)
}
```

## Finding Page IDs

```bash
curl -s http://localhost:9100/json | jq '.[] | select(.type=="page") | .id, .title'
```

## Running

```bash
# Start BrowserOS (if not running)
nohup browseros --headless --no-sandbox --disable-gpu --remote-debugging-port=9100 > /tmp/browseros.log 2>&1 &
```