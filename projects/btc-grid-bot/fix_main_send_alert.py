#!/usr/bin/env python3

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'r') as f:
    lines = f.readlines()

# The problematic send_alert is around line 95-101 (0-indexed: 94-100)
# Replace lines 94-100 with corrected version
new_send_alert = '''                    await send_alert(
                        (
                            f"📊 Daily PnL Report · {now.strftime('%Y-%m-%d %H:%M UTC')}\\n"
                            f"Starting Equity: ${cfg['capital']['starting_equity']:.2f}\\n"
                            f"Current Equity: ${equity:.2f}\\n"
                            f"Daily PnL: ${pnl:.2f} ({pnl/cfg['capital']['starting_equity']*100:.1f}%)\n"
                            f"Grid Range: ${gm.state['range_low']:.0f}–${gm.state['range_high']:.0f}"
                        )
                    )
'''

# Replace lines 94-100 (7 lines)
lines[94:101] = [new_send_alert + '\n']

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'w') as f:
    f.writelines(lines)

print("Fixed main.py send_alert multiline f-string")