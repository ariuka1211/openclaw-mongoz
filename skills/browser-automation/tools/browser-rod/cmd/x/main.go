package main

import (
	"fmt"
	"os"
	"browser-rod"
)

func main() {
	wsURL := "ws://localhost:9100/devtools/page/EB1F8B85ED643E0AFDCB4BDEC505C0EB"
	
	client, err := browser.New(wsURL)
	if err != nil {
		fmt.Printf("❌ Connect failed: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()
	
	fmt.Println("🌐 Loading X post...")
	client.Navigate("https://x.com/modal/status/2041820290716729379")
	client.Wait(8)
	
	img, _ := client.Screenshot(true)
	os.WriteFile("/root/.openclaw/workspace/x-post.png", img, 0644)
	fmt.Printf("✅ Saved: %d bytes\n", len(img))
}