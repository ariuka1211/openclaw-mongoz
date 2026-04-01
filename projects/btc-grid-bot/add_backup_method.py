#!/usr/bin/env python3
import re

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'r') as f:
    content = f.read()

# New _redeploy_backup method
new_method = '''    async def _redeploy_backup(self, backup_state: dict):
        """Redeploy the grid from a backup state (used when roll fails)."""
        logging.info("Reverting to backup grid state")
        
        # Cancel any current orders (if any)
        try:
            await self.api.cancel_all_orders()
        except Exception as e:
            logging.error(f"Failed to cancel orders during revert: {e}")
            # Continue anyway - we'll try to redeploy on top of possibly existing orders
        
        # Restore state from backup
        self.state = backup_state.copy()
        
        # Redeploy using the backup levels and size
        try:
            await self.deploy(
                {"buy_levels": backup_state["levels"]["buy"], "sell_levels": backup_state["levels"]["sell"], "range_low": backup_state["range_low"], "range_high": backup_state["range_high"]},
                backup_state["equity_at_reset"] if "equity_at_reset" in backup_state else await self.api.get_equity(),
                await self.api.get_btc_price()
            )
        except Exception as e:
            logging.error(f"Failed to redeploy backup grid: {e}")
            await send_alert(f"⚠️ Failed to restore grid after roll failure: {e}")
            # If we can't redeploy, pause the bot
            self.state["paused"] = True
            self.state["active"] = False
            self.state["pause_reason"] = f"Backup redeploy failed: {e}"
            self._save_state()
'''

# Add the method before the _pause method (find _pause and insert before it)
pattern = r'(    async def _pause\(self, reason: str\):)'
replacement = new_method + '\n' + r'\1'

new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'w') as f:
    f.write(new_content)

print("Added _redeploy_backup method to grid.py")