#!/usr/bin/env python3
import re

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'r') as f:
    content = f.read()

# Add necessary imports at the top
import_section = '''import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import yaml
from dotenv import load_dotenv
'''

# Replace the import section
content = content.replace("""import asyncio
import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
""", import_section)

# New code for PnL reporting and loss limit check
new_code = '''            # Check if it's time for daily PnL report (23:30 UTC)
            now = datetime.now(timezone.utc)
            if now.hour == 23 and now.minute == 30:
                try:
                    equity = await api.get_equity()
                    pnl = equity - cfg["capital"]["starting_equity"]
                    await send_alert(
                        f"📊 Daily PnL Report · {now.strftime('%Y-%m-%d %H:%M UTC')}\\n"
                        f"Starting Equity: ${cfg['capital']['starting_equity']:.2f}\\n"
                        f"Current Equity: ${equity:.2f}\\n"
                        f"Daily PnL: ${pnl:.2f} ({pnl/cfg['capital']['starting_equity']*100:.1f}%)\\n"
                        f"Grid Range: ${gm.state['range_low']:.0f}–${gm.state['range_high']:.0f}"
                    )
                    # Wait until the next day to avoid duplicate reports
                    tomorrow = now.replace(hour=23, minute=30, second=0, microsecond=0) + timedelta(days=1)
                    await asyncio.sleep((tomorrow - now).total_seconds())
                except Exception as e:
                    logging.error(f"Failed to send daily PnL report: {e}")

            # Check daily loss limit (8% drop from equity at reset)
            if gm.state["active"] and not gm.state["paused"]:
                equity = await api.get_equity()
                equity_at_reset = gm.state.get("equity_at_reset", equity)
                loss_pct = (equity_at_reset - equity) / equity_at_reset
                if loss_pct > cfg["risk"]["daily_loss_limit_pct"]:
                    await gm._pause(f"Daily loss limit hit: {loss_pct:.1%} drop from reset equity")
                    await send_alert(f"🚨 Daily loss limit reached!\\n"
                                     f"Equity dropped {loss_pct:.1%} from reset.\\n"
                                     f"Starting: ${equity_at_reset:.2f} → Now: ${equity:.2f}")
                    return
'''

# Insert this code into the run_loop function after check_fills
# Find the pattern: "await gm.check_fills(price)" and then insert after that block.
# We need to be careful to maintain proper indentation.

pattern = r'(            await gm\.check_fills\(price\)\n)'
replacement = r'\1' + new_code + '\n'

content = re.sub(pattern, replacement, content, flags=re.DOTALL)

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'w') as f:
    f.write(content)

print("Updated main.py with imports, daily PnL reporting, and loss limit check")