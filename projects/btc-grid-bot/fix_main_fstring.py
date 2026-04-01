#!/usr/bin/env python3
import re

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'r') as f:
    content = f.read()

# Fix the multiline f-string in daily PnL report
old_broken = '''                    await send_alert(
                        f"📊 Daily PnL Report · {now.strftime('%Y-%m-%d %H:%M UTC')}
"
                        f"Starting Equity: ${cfg['capital']['starting_equity']:.2f}
"
                        f"Current Equity: ${equity:.2f}
"
                        f"Daily PnL: ${pnl:.2f} ({pnl/cfg['capital']['starting_equity']*100:.1f}%)
"
                        f"Grid Range: ${gm.state['range_low']:.0f}–${gm.state['range_high']:.0f}"
                    )'''

new_fixed = '''                    await send_alert(
                        (
                            f"📊 Daily PnL Report · {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
                            f"Starting Equity: ${cfg['capital']['starting_equity']:.2f}\n"
                            f"Current Equity: ${equity:.2f}\n"
                            f"Daily PnL: ${pnl:.2f} ({pnl/cfg['capital']['starting_equity']*100:.1f}%)\n"
                            f"Grid Range: ${gm.state['range_low']:.0f}–${gm.state['range_high']:.0f}"
                        )
                    )
'''

content = content.replace(old_broken, new_fixed)

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'w') as f:
    f.write(content)

print("Fixed main.py f-string syntax error")