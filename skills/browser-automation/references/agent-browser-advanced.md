# Agent Browser Advanced Reference

## Complete Command Reference

### Core Navigation
```bash
agent-browser open <url>                    # Navigate to URL
agent-browser snapshot                      # Get accessibility tree  
agent-browser snapshot -i                  # Interactive elements only
agent-browser snapshot -i --urls           # Include link URLs
agent-browser click @e1                    # Click by ref
agent-browser fill @e2 "text"              # Fill by ref
agent-browser screenshot --annotate        # Numbered screenshot
```

### AI Chat Mode
```bash
agent-browser chat "navigate and fill login form"  # Single instruction
agent-browser chat                                  # Interactive REPL
agent-browser -q chat "summarize this page"        # Quiet mode
agent-browser -v chat "complex workflow"           # Verbose mode
```

### Session Management
```bash
agent-browser --session myapp open site.com       # Named session
agent-browser --session-name persistent open site # Auto-save state
agent-browser --profile Default open gmail.com    # Use Chrome profile
agent-browser state save ./auth.json              # Save auth state
agent-browser state load ./auth.json              # Load auth state
```

### Mobile Testing
```bash
agent-browser -p ios --device "iPhone 16 Pro" open site.com
agent-browser -p ios snapshot -i
agent-browser -p ios tap @e1
agent-browser -p ios swipe up
agent-browser -p ios screenshot mobile.png
```

### Advanced Features
```bash
agent-browser network route --mock response.json  # Mock API
agent-browser network har start                   # Record traffic
agent-browser eval "window.location.href"         # Run JS
agent-browser wait --text "Welcome"               # Wait for content
agent-browser diff screenshot --baseline old.png  # Visual diff
agent-browser batch "cmd1" "cmd2" "cmd3"          # Multiple commands
```

## Best Practices

### Workflow Pattern
1. **Navigate**: `agent-browser open <url>`
2. **Snapshot**: `agent-browser snapshot -i` (get refs)
3. **Interact**: `agent-browser click @e1` / `fill @e2 "text"`
4. **Verify**: Check page changes, re-snapshot if needed
5. **Extract**: Get data or screenshot results

### Ref-Based Interaction
- Always use `snapshot -i` before interactions
- Use `@e1`, `@e2` refs from snapshot output
- Re-snapshot after page changes (navigation, AJAX)
- Refs are cached until next snapshot

### Error Handling
```bash
# Wait for elements to appear
agent-browser wait "#login-form"

# Handle dynamic content
agent-browser wait --load networkidle

# Retry on failure
agent-browser click @e1 || agent-browser snapshot -i && agent-browser click @e1
```

### Performance Tips
- Use `snapshot -i` instead of full snapshot for interactions
- Use `--headless` for faster execution (default)
- Use `batch` for multiple commands
- Cache authentication with `--session-name`

## Integration Examples

### Trading Bot Integration
```bash
# Monitor liquidations
agent-browser open "https://coinglass.com/liquidations"
agent-browser snapshot -i | grep -o '@e[0-9]*.*liquidation' 
agent-browser get text @e5  # Extract liquidation value

# TradingView analysis
agent-browser open "https://tradingview.com/chart"
agent-browser chat "add RSI indicator and take screenshot"
```

### Form Automation
```bash
# Job application
agent-browser open "company.com/careers"
agent-browser find text "Software Engineer" click
agent-browser find label "Name" fill "John Doe" 
agent-browser find label "Email" fill "john@example.com"
agent-browser upload "#resume" "./resume.pdf"
agent-browser find role button click --name "Submit"
```

### QA Testing
```bash
# Login flow test
agent-browser --session test-user open "app.com/login"
agent-browser fill "@e1" "test@example.com"
agent-browser fill "@e2" "password123"  
agent-browser click "@e3"
agent-browser wait --text "Dashboard"
agent-browser screenshot login-success.png
```

## Troubleshooting

### Common Issues
- **Elements not found**: Re-run `snapshot -i` to refresh refs
- **Timeout errors**: Increase wait time or use `wait --load`
- **Mobile not working**: Ensure Xcode and iOS Simulator installed
- **Auth failures**: Check session persistence with `--session-name`

### Debug Commands
```bash
agent-browser --debug open site.com        # Debug output
agent-browser console                       # Browser console logs
agent-browser errors                        # Page errors  
agent-browser get cdp-url                  # DevTools URL
agent-browser inspect                       # Open DevTools
```