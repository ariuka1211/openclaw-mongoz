#!/usr/bin/env python3
import re

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'r') as f:
    content = f.read()

# New roll_grid method
new_method = '''    async def roll_grid(self, current_price: float):
        """Roll the grid to follow price when it hits band edges.

        - Cancel old orders (backup state first)
        - Fetch fresh candle data and calculate indicators
        - Generate new grid levels from bands + ATR + skew
        - Deploy new grid
        - If any step fails, revert to previous grid state
        """
        logging.info(f"Rolling grid at ${current_price:,.0f}")
        
        # Backup current state in case we need to revert
        backup_state = self.state.copy()
        
        # Cancel existing orders first
        cancelled = await self.api.cancel_all_orders()
        logging.info(f"Cancelled {cancelled} old orders for roll")
        
        # Fetch fresh candles for indicator recalculation
        try:
            from analyst import fetch_candles
            from market_intel import gather_all_intel
            from indicators import gather_indicators
            
            candles_15m = await fetch_candles("15m", limit=200)
            candles_4h = await fetch_candles("4H", limit=48)
            market_intel = await gather_all_intel(self.cfg)
            
            indicators = gather_indicators(candles_15m, candles_15m, candles_4h, market_intel)
            bands = indicators["bollinger"]
            atr = indicators["atr"]
            skew = indicators["skew"]
        except Exception as e:
            logging.error(f"Failed to fetch indicators for roll: {e}")
            await send_alert(f"⚠️ Grid roll failed: {e}. Reverting to previous grid.")
            await self._redeploy_backup(backup_state)
            return
        
        # Generate new levels from bands + ATR + skew
        new_levels = self.generate_levels_from_bands(bands, atr, skew, current_price)
        
        if not new_levels["buy_levels"] or not new_levels["sell_levels"]:
            logging.warning("Roll generated empty levels — reverting")
            await send_alert("⚠️ Grid roll generated no levels. Reverting to previous grid.")
            await self._redeploy_backup(backup_state)
            return
        
        # Get equity for sizing
        try:
            equity = await self.api.get_equity()
        except Exception as e:
            logging.error(f"Failed to get equity for roll: {e}")
            await send_alert(f"⚠️ Grid roll failed to fetch equity: {e}. Reverting.")
            await self._redeploy_backup(backup_state)
            return
        
        # Deploy new grid (reuses the deploy method)
        try:
            await self.deploy(new_levels, equity, current_price)
        except Exception as e:
            logging.error(f"Failed to deploy new grid: {e}")
            await send_alert(f"⚠️ Grid roll failed to deploy: {e}. Reverting to previous grid.")
            await self._redeploy_backup(backup_state)
            return
        
        # Track roll
        self.state["roll_count"] = self.state.get("roll_count", 0) + 1
        self.state["last_roll"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        
        direction = "⬆️" if current_price > self.state.get("range_low", 0) else "⬇️"
        await send_alert(
            f"🔄 Grid rolled {direction} · BTC @ ${current_price:,.0f}\n"
            f"New range: ${min(new_levels['buy_levels']):,.0f} – ${max(new_levels['sell_levels']):,.0f}\n"
            f"Skew: {skew['buy_pct']}% buy / {skew['sell_pct']}% sell\n"
            f"Spacing: ${atr['suggested_spacing']:,.0f} (ATR-based)"
        )
'''

# Find the roll_grid method and replace it
# Pattern to match the entire roll_grid method (from def to the end of its last line)
# We'll use a non-greedy match until we find the next method or class member
pattern = r'    async def roll_grid\(self, current_price: float\):.*?(?=\n    [a-z_]+ =|\n    async def |\n    def |\nclass |\Z)'
replacement = new_method

new_content = re.sub(pattern, new_method, content, flags=re.DOTALL)

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'w') as f:
    f.write(new_content)

print("Grid.py updated successfully!")