# Browserbase API Reference

## Authentication
- **Header**: `x-bb-api-key`
- **API Key**: `bb_live_pWCC50dAT24TvssmuHToAXx4iQY`
- **Base URL**: `https://api.browserbase.com/v1`

## Fetch API

### Basic Usage
```bash
curl -X POST "https://api.browserbase.com/v1/fetch" \
  -H "x-bb-api-key: bb_live_pWCC50dAT24TvssmuHToAXx4iQY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "waitForTimeout": 3000}'
```

### Request Parameters
```json
{
  "url": "https://example.com",          // Required: Target URL
  "waitForTimeout": 3000,                // Optional: Wait time in ms
  "userAgent": "custom-agent/1.0",       // Optional: Custom user agent
  "headers": {                           // Optional: Custom headers
    "Authorization": "Bearer token"
  },
  "viewport": {                          // Optional: Browser viewport
    "width": 1280,
    "height": 720
  }
}
```

### Response Format
```json
{
  "content": "<html>...</html>",         // Page HTML content
  "url": "https://example.com",          // Final URL (after redirects)
  "status": 200,                         // HTTP status code
  "headers": {                           // Response headers
    "content-type": "text/html"
  },
  "timing": {                            // Performance metrics
    "total": 892,                        // Total time in ms
    "network": 234,                      // Network time
    "processing": 658                    // Processing time
  }
}
```

## Sessions API

### Create Session
```bash
curl -X POST "https://api.browserbase.com/v1/sessions" \
  -H "x-bb-api-key: bb_live_pWCC50dAT24TvssmuHToAXx4iQY" \
  -H "Content-Type: application/json" \
  -d '{"projectId": "your-project"}'
```

### Use Session
```bash
curl -X POST "https://api.browserbase.com/v1/sessions/{sessionId}/fetch" \
  -H "x-bb-api-key: bb_live_pWCC50dAT24TvssmuHToAXx4iQY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

## Error Handling

### Common Errors
- **401 Unauthorized**: Invalid API key
- **404 Not Found**: URL not accessible
- **408 Request Timeout**: Page took too long to load
- **429 Too Many Requests**: Rate limited
- **500 Internal Server Error**: Browserbase service issue

### Error Response Format
```json
{
  "error": {
    "code": "TIMEOUT",
    "message": "Page failed to load within 30 seconds",
    "details": {
      "url": "https://slow-site.com",
      "timeout": 30000
    }
  }
}
```

## Rate Limits
- **Free Tier**: 100 requests/hour
- **Paid Plans**: Based on subscription
- **Rate Limit Headers**: Check `X-RateLimit-*` headers
- **Backoff**: Implement exponential backoff on 429 errors

## Best Practices

### Performance Optimization
```json
{
  "url": "https://data-site.com",
  "waitForTimeout": 1000,              // Minimal wait for data sites
  "blockResources": ["images", "fonts"], // Block unnecessary resources
  "viewport": {"width": 1024, "height": 768} // Smaller viewport
}
```

### Trading Data Extraction
```json
{
  "url": "https://coinglass.com/liquidations/BTC",
  "waitForTimeout": 3000,              // Wait for charts to load
  "headers": {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
  }
}
```

### Mobile Simulation
```json
{
  "url": "https://mobile-site.com",
  "viewport": {"width": 375, "height": 667}, // iPhone viewport
  "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)"
}
```

## Integration Examples

### Python Integration
```python
import requests

def browserbase_fetch(url, wait=3000):
    response = requests.post(
        "https://api.browserbase.com/v1/fetch",
        headers={
            "x-bb-api-key": "bb_live_pWCC50dAT24TvssmuHToAXx4iQY",
            "Content-Type": "application/json"
        },
        json={"url": url, "waitForTimeout": wait}
    )
    return response.json()
```

### JavaScript Integration
```javascript
async function browserbaseFetch(url, options = {}) {
  const response = await fetch('https://api.browserbase.com/v1/fetch', {
    method: 'POST',
    headers: {
      'x-bb-api-key': 'bb_live_pWCC50dAT24TvssmuHToAXx4iQY',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      url,
      waitForTimeout: options.wait || 3000,
      ...options
    })
  });
  
  return response.json();
}
```

### Bash/cURL Integration
```bash
#!/bin/bash
browserbase_fetch() {
  local url="$1"
  local wait="${2:-3000}"
  
  curl -s -X POST "https://api.browserbase.com/v1/fetch" \
    -H "x-bb-api-key: bb_live_pWCC50dAT24TvssmuHToAXx4iQY" \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"$url\", \"waitForTimeout\": $wait}"
}
```

## Troubleshooting

### Debug Mode
```json
{
  "url": "https://problematic-site.com",
  "debug": true,                        // Enable debug output
  "screenshot": true,                   // Include screenshot
  "console": true                       // Include console logs
}
```

### Common Issues
1. **JavaScript not loading**: Increase `waitForTimeout`
2. **Captcha blocking**: Use different user agents
3. **Rate limiting**: Implement backoff and retry logic
4. **Large pages**: Use resource blocking to speed up loading

### Monitoring
- Check response times in `timing` object
- Monitor rate limit headers
- Log errors for debugging
- Set up alerts for 5xx errors