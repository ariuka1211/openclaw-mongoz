package main

import (
	"fmt"
	"os"
	"time"
	"browser-rod"
)

func main() {
	wsURL := "ws://localhost:9100/devtools/page/38D67C1E47E422379AC7166C7BABD87F"
	
	client, err := browser.New(wsURL)
	if err != nil {
		fmt.Printf("❌ Connect failed: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()
	
	fmt.Println("🌐 Loading Modal GLM docs...")
	client.Navigate("https://modal.com/docs/guide/glm")
	time.Sleep(8)
	
	img, _ := client.Screenshot(true)
	os.WriteFile("/root/.openclaw/workspace/glm-docs-full.png", img, 0644)
	fmt.Printf("✅ Saved: %d bytes\n", len(img))
}