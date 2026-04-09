package main

import (
	"fmt"
	"os"
	"time"
	"browser-rod"
)

func main() {
	wsURL := "ws://localhost:9100/devtools/page/28A66C076B6AF3713ADBD2187BE6F4CE"
	
	client, err := browser.New(wsURL)
	if err != nil {
		fmt.Printf("❌ Connect failed: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()
	
	// Search for GLM-5.1 on Modal docs
	fmt.Println("🌐 Searching Modal docs for GLM-5...")
	client.Navigate("https://modal.com/docs?query=GLM-5")
	time.Sleep(8)
	
	img, _ := client.Screenshot(true)
	os.WriteFile("/root/.openclaw/workspace/modal-search-glm.png", img, 0644)
	fmt.Printf("✅ Saved: %d bytes\n", len(img))
}
