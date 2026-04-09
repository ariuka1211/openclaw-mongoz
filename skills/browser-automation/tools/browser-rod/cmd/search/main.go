package main

import (
	"fmt"
	"os"
	"time"
	"browser-rod"
)

func main() {
	wsURL := "ws://localhost:9100/devtools/page/C955827E638EA485AFB445F5A3699255"
	
	client, err := browser.New(wsURL)
	if err != nil {
		fmt.Printf("❌ Connect failed: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()
	
	fmt.Println("🌐 Searching OpenRouter for GLM...")
	client.Navigate("https://openrouter.ai/models?query=glm")
	time.Sleep(8)
	
	img, _ := client.Screenshot(true)
	os.WriteFile("/root/.openclaw/workspace/openrouter-glm.png", img, 0644)
	fmt.Printf("✅ Saved: %d bytes\n", len(img))
}
