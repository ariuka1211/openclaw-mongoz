#!/usr/bin/env python3

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'r') as f:
    lines = f.readlines()

# Find the lines to replace (around line 405-414)
# We'll replace lines 404-414 (0-indexed: 403-413)
new_send_alert = '''        await send_alert(
            (
                f"🔄 Grid rolled {direction} · BTC @ ${current_price:,.0f}\\n"
                f"New range: ${min(new_levels['buy_levels']):,.0f} – ${max(new_levels['sell_levels']):,.0f}\\n"
                f"Skew: {skew['buy_pct']}% buy / {skew['sell_pct']}% sell\\n"
                f"Spacing: ${atr['suggested_spacing']:,.0f} (ATR-based)"
            )
        )
'''

# Replace lines 403-413 (10 lines total)
lines[403:413] = [new_send_alert + '\n']

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'w') as f:
    f.writelines(lines)

print("Fixed grid.py send_alert multiline f-string")