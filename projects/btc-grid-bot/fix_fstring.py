#!/usr/bin/env python3
import re

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'r') as f:
    content = f.read()

# Fix the f-string that spans multiple lines
# Replace the broken f-string with a properly formatted one using parentheses
old_broken = '''        await send_alert(
            f"🔄 Grid rolled {direction} · BTC @ ${current_price:,.0f}\n"
            f"New range: ${min(new_levels['buy_levels']):,.0f} – ${max(new_levels['sell_levels']):,.0f}\n"
            f"Skew: {skew['buy_pct']}% buy / {skew['sell_pct']}% sell\n"
            f"Spacing: ${atr['suggested_spacing']:,.0f} (ATR-based)"
        )'''

new_fixed = '''        await send_alert(
            f"🔄 Grid rolled {direction} · BTC @ ${current_price:,.0f}\n"
            f"New range: ${min(new_levels['buy_levels']):,.0f} – ${max(new_levels['sell_levels']):,.0f}\n"
            f"Skew: {skew['buy_pct']}% buy / {skew['sell_pct']}% sell\n"
            f"Spacing: ${atr['suggested_spacing']:,.0f} (ATR-based)"
        )
'''

content = content.replace(old_broken, new_fixed)

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'w') as f:
    f.write(content)

print("Fixed grid.py f-string syntax error")