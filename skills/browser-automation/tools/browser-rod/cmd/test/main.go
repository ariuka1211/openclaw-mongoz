package main

import (
	"fmt"
	"browser-rod"
)

func main() {
	fmt.Println("🧪 Testing browser-rod package...")
	
	// Use the page URL from BrowserOS
	client, err := browser.New("ws://localhost:9100/devtools/page/952F56BA5CC529714D978A7AC9E21D1E")
	if err != nil {
		fmt.Printf("❌ Failed to connect: %v\n", err)
		return
	}
	defer client.Close()
	
	fmt.Println("✅ Connected to BrowserOS")
	
	// Test navigate
	err = client.Navigate("https://httpbin.org/html")
	if err != nil {
		fmt.Printf("❌ Navigate failed: %v\n", err)
		return
	}
	fmt.Println("✅ Navigate works")
	
	// Test HTML
	html, err := client.HTML()
	if err != nil {
		fmt.Printf("❌ HTML failed: %v\n", err)
		return
	}
	fmt.Printf("✅ HTML retrieved: %d chars\n", len(html))
	
	// Test screenshot
	img, err := client.Screenshot(true)
	if err != nil {
		fmt.Printf("❌ Screenshot failed: %v\n", err)
		return
	}
	fmt.Printf("✅ Screenshot: %d bytes\n", len(img))
	
	fmt.Println("\n🎉 browser-rod package tests passed!")
}