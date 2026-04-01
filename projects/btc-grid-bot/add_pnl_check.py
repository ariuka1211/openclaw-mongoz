#!/usr/bin/env python3
import re

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'r') as f:
    content = f.read()

# New code to add PnL reporting
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
                        f"Grid Range: ${self.state['range_low']:.0f}–${self.state['range_high']:.0f}"
                    )
                    # Wait until the next day to avoid duplicate reports
                    tomorrow = now.replace(hour=23, minute=30, second=0, microsecond=0) + timedelta(days=1)
                    await asyncio.sleep((tomorrow - now).total_seconds())
                except Exception as e:
                    logging.error(f"Failed to send daily PnL report: {e}")

            # Check daily loss limit (8% drop from equity at reset)
            if self.state["active"] and not self.state["paused"]:
                equity = await api.get_equity()
                equity_at_reset = self.state.get("equity_at_reset", equity)
                loss_pct = (equity_at_reset - equity) / equity_at_reset
                if loss_pct > cfg["risk"]["daily_loss_limit_pct"]:
                    await self._pause(f"Daily loss limit hit: {loss_pct:.1%} drop from reset equity")
                    await send_alert(f"🚨 Daily loss limit reached!\\n"
                                     f"Equity dropped {loss_pct:.1%} from reset.\\n"
                                     f"Starting: ${equity_at_reset:.2f} → Now: ${equity:.2f}")
                    return
'''

# Insert this code into the run_loop function after the try block but before the except
# Find the line "while True:" and then insert after the "await asyncio.sleep(poll_interval)" line?
# Actually, we need to insert inside the while loop, after check_fills and before the except.
# Let's find the pattern: "await gm.check_fills(price)" and then insert after that block.

pattern = r'(            await gm\.check_fills\(price\)\n)'
replacement = r'\1' + new_code + '\n'

new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'w') as f:
    f.write(new_content)

print("Added daily PnL reporting and loss limit check to main.py")