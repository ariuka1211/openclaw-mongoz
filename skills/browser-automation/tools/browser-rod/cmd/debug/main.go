package main

import (
	"fmt"
	"os"
	"time"
	"browser-rod"
)

func main() {
	wsURL := "ws://localhost:9100/devtools/page/AE37ADC9CF643F7829D615799C51F0E8"
	
	client, err := browser.New(wsURL)
	if err != nil {
		fmt.Printf("❌ Connect failed: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()
	
	fmt.Println("🌐 Navigating to coinglass.com...")
	client.Navigate("https://coinglass.com")
	time.Sleep(5 * time.Second)
	
	html, _ := client.HTML()
	fmt.Printf("📄 HTML length: %d\n", len(html))
	
	// Check if 404 in HTML
	if len(html) < 1000 {
		fmt.Printf("⚠️ Short HTML: %s\n", html[:min(200, len(html))])
	}
	
	img, _ := client.Screenshot(true)
	os.WriteFile("/root/.openclaw/workspace/coinglass-test.png", img, 0644)
	fmt.Printf("✅ Screenshot: %d bytes\n", len(img))
}
