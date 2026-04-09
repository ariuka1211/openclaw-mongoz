package browser

import (
	"fmt"
	"time"

	"github.com/go-rod/rod"
)

// Client wraps go-rod for BrowserOS CDP connections
type Client struct {
	browser *rod.Browser
	page    *rod.Page
}

// New creates a new BrowserOS client
func New(cdpURL string) (*Client, error) {
	browser := rod.New().ControlURL(cdpURL).MustConnect()
	page := browser.MustPage("about:blank")

	return &Client{
		browser: browser,
		page:    page,
	}, nil
}

// NewFromPage creates a client for a specific page
func NewFromPage(cdpURL, pageID string) (*Client, error) {
	return New("ws://localhost:9100/devtools/page/" + pageID)
}

// Navigate navigates to a URL
func (c *Client) Navigate(url string) error {
	c.page.Navigate(url)
	c.page.MustWaitLoad()
	return nil
}

// HTML returns the page HTML
func (c *Client) HTML() (string, error) {
	html, err := c.page.HTML()
	return html, err
}

// Screenshot takes a screenshot of the page
func (c *Client) Screenshot(fullPage bool) ([]byte, error) {
	img, err := c.page.Screenshot(fullPage, nil)
	return img, err
}

// Click clicks an element by selector
func (c *Client) Click(selector string) error {
	el := c.page.MustElement(selector)
	el.MustClick()
	return nil
}

// Close closes the browser connection
func (c *Client) Close() error {
	c.browser.Close()
	return nil
}

// Timing holds performance timing information
type Timing struct {
	Connect    time.Duration
	Navigate   time.Duration
	Screenshot time.Duration
	HTML       time.Duration
}

// Benchmark runs a quick benchmark against BrowserOS
func Benchmark(cdpURL string) (*Timing, error) {
	t := &Timing{}

	start := time.Now()
	browser := rod.New().ControlURL(cdpURL).MustConnect()
	t.Connect = time.Since(start)
	defer browser.Close()

	page := browser.MustPage("about:blank")

	// Navigate to example.com
	start = time.Now()
	page.Navigate("http://example.com")
	page.MustWaitLoad()
	t.Navigate = time.Since(start)

	// Screenshot
	start = time.Now()
	_, err := page.Screenshot(true, nil)
	if err != nil {
		return nil, err
	}
	t.Screenshot = time.Since(start)

	// HTML
	start = time.Now()
	page.HTML()
	t.HTML = time.Since(start)

	return t, nil
}

// Log prints to console
func Log(format string, v ...interface{}) {
	fmt.Printf(format+"\n", v...)
}