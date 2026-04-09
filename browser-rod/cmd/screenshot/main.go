package main

import (
	"fmt"
	"os"
	"time"
	"browser-rod"
)

func main() {
	fmt.Println("📸 Taking CoinGlass Liquidation Heatmap...")
	
	wsURL := "ws://localhost:9100/devtools/page/A72E38906161141B57D8C7971A1866C8"
	
	client, err := browser.New(wsURL)
	if err != nil {
		fmt.Printf("❌ Connect failed: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()
	
	// Direct URL to liq heatmap
	fmt.Println("🌐 Navigating to liquidation heatmap...")
	client.Navigate("https://www.coinglass.com/topic/liquidation-heatmap")
	time.Sleep(6 * time.Second)
	
	img, err := client.Screenshot(true)
	if err != nil {
		fmt.Printf("❌ Screenshot failed: %v\n", err)
		os.Exit(1)
	}
	
	err = os.WriteFile("/root/.openclaw/workspace/coinglass-heatmap.png", img, 0644)
	if err != nil {
		fmt.Printf("❌ Save failed: %v\n", err)
		os.Exit(1)
	}
	
	fmt.Printf("✅ Saved: %d bytes\n", len(img))
}
