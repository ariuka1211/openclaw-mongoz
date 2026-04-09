package main

import (
	"fmt"
	"os"
	"time"
	"browser-rod"
)

func main() {
	wsURL := "ws://localhost:9100/devtools/page/FF92776A828F1BA4E754969DF5FB92F7"
	
	client, err := browser.New(wsURL)
	if err != nil {
		fmt.Printf("❌ Connect failed: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()
	
	// Try apps page
	fmt.Println("🌐 Loading modal.com/apps...")
	client.Navigate("https://modal.com/apps")
	time.Sleep(8)
	
	img, _ := client.Screenshot(true)
	os.WriteFile("/root/.openclaw/workspace/modal-apps.png", img, 0644)
	fmt.Printf("✅ Saved: %d bytes\n", len(img))
}
