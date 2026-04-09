package main

import (
	"fmt"
	"os"
	"browser-rod"
)

func main() {
	wsURL := "ws://localhost:9100/devtools/page/C0097B0B8520BBF2A3AF851AFED6A5ED"
	
	client, err := browser.New(wsURL)
	if err != nil {
		fmt.Printf("❌ Connect failed: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()
	
	// Try just navigating to coinglass and finding the heatmap from menu
	fmt.Println("🌐 Navigating to coinglass.com...")
	client.Navigate("https://coinglass.com")
	client.Wait(6)
	
	// Take screenshot
	img, _ := client.Screenshot(true)
	os.WriteFile("/root/.openclaw/workspace/liq-v3.png", img, 0644)
	fmt.Printf("✅ Saved v3: %d bytes\n", len(img))
	
	// Print HTML for debugging
	html, _ := client.HTML()
	fmt.Printf("📄 HTML: %d chars\n", len(html))
}
