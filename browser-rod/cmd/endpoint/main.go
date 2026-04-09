package main

import (
	"fmt"
	"os"
	"time"
	"browser-rod"
)

func main() {
	wsURL := "ws://localhost:9100/devtools/page/684018977C17F7194CD5D11287D8DDDC"
	
	client, err := browser.New(wsURL)
	if err != nil {
		fmt.Printf("❌ Connect failed: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()
	
	// First load modal.com home to establish session
	fmt.Println("🌐 Loading modal.com...")
	client.Navigate("https://modal.com")
	time.Sleep(5)
	
	// Then navigate to the endpoint page
	fmt.Println("🌐 Loading /glm-5-endpoint...")
	client.Navigate("https://modal.com/glm-5-endpoint")
	time.Sleep(15)  // Wait longer for content to load
	
	img, _ := client.Screenshot(true)
	os.WriteFile("/root/.openclaw/workspace/glm-endpoint-v2.png", img, 0644)
	fmt.Printf("✅ Saved: %d bytes\n", len(img))
}
