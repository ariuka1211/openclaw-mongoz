"""
Order pacing, quota management, and cache maintenance.

Handles order submission rate limiting, volume quota checks,
and periodic cache cleanup.
"""

import logging
import time

from config import BotConfig
from api.lighter_api import LighterAPI


class OrderManager:
    def __init__(self, cfg: BotConfig, api: LighterAPI, bot):
        """Initialize order manager.

        Args:
            cfg: Bot configuration
            api: LighterAPI instance
            bot: LighterCopilot reference (for state)
        """
        self.cfg = cfg
        self.api = api
        self.bot = bot

    def _prune_caches(self):
        """Remove expired entries from in-memory caches to prevent unbounded growth."""
        now = time.monotonic()
        # Prune AI close cooldown (entries older than cooldown period)
        expired_cooldowns = [s for s, t in self.bot._ai_close_cooldown.items() if t < now]
        for s in expired_cooldowns:
            del self.bot._ai_close_cooldown[s]
        # Prune API lag warnings (entries older than 1 hour)
        expired_warnings = [s for s, t in self.bot._api_lag_warnings.items() if t < now - 3600]
        for s in expired_warnings:
            del self.bot._api_lag_warnings[s]
        # Prune recently closed positions (entries older than TTL)
        expired_closed = [m for m, t in self.bot._recently_closed.items() if t < now]
        for m in expired_closed:
            del self.bot._recently_closed[m]
        # Prune close attempt cooldowns (entries older than cooldown period)
        expired_close_cd = [s for s, t in self.bot._close_attempt_cooldown.items() if t < now]
        for s in expired_close_cd:
            del self.bot._close_attempt_cooldown[s]
        # Fix #15: Prune _close_attempts for symbols whose cooldown has expired
        # (counter is only useful while cooldown is active)
        for s in list(self.bot._close_attempts.keys()):
            if s not in self.bot._close_attempt_cooldown:
                del self.bot._close_attempts[s]
        # Prune DSL close attempt cooldowns
        expired_dsl_close_cd = [s for s, t in self.bot._dsl_close_attempt_cooldown.items() if t < now]
        for s in expired_dsl_close_cd:
            del self.bot._dsl_close_attempt_cooldown[s]
        # Prune symbol cache (entries older than TTL)
        if self.api:
            expired_symbols = [mid for mid, (ts, _) in self.api._symbol_cache.items() if (now - ts) > self.api._symbol_cache_ttl]
            for mid in expired_symbols:
                del self.api._symbol_cache[mid]
        # Prune no-price ticks for positions no longer tracked
        orphaned_mids = [m for m in self.bot._no_price_ticks if m not in self.bot.tracker.positions]
        for m in orphaned_mids:
            del self.bot._no_price_ticks[m]
        # MED-7: Remove bot_managed_market_ids where market is no longer tracked
        # and not in recently closed (manual closes remove from tracker but didn't clean this set)
        stale_managed = [m for m in self.bot.bot_managed_market_ids
                         if m not in self.bot.tracker.positions and m not in self.bot._recently_closed]
        for m in stale_managed:
            self.bot.bot_managed_market_ids.discard(m)
        if stale_managed:
            logging.debug(f"🧹 Pruned {len(stale_managed)} stale bot_managed_market_ids")



    def _should_pace_orders(self) -> bool:
        """Pace orders to leverage 15-second free tx window when quota is low."""
        if self.api and self.api.volume_quota_remaining is not None and self.api.volume_quota_remaining < 35:
            time_since_last = time.time() - self.bot._last_order_time
            if time_since_last < 16:  # 16s to be safe
                logging.debug(f"⏱️ Pacing orders (quota={self.api.volume_quota_remaining}, last_order={time_since_last:.1f}s ago)")
                return True
        return False



    def _should_skip_open_for_quota(self) -> bool:
        """Skip new opens when quota is low to preserve it for SL orders."""
        quota = self.api.volume_quota_remaining if self.api else None
        if quota is not None and quota < 35:
            logging.debug(f"🚫 Skipping new opens (quota={quota} < 35, preserving for SL)")
            return True
        return False



    def _is_quota_emergency(self) -> bool:
        """Emergency mode when quota is critically low."""
        quota = self.api.volume_quota_remaining if self.api else None
        return quota is not None and quota < 5



    def _should_skip_non_critical_orders(self) -> bool:
        """In emergency mode, only allow SL orders."""
        if self._is_quota_emergency():
            # Rate-limit warning to once per minute
            now = time.monotonic()
            if now - getattr(self, '_last_quota_emergency_warn', 0) > 60:
                self.bot._last_quota_emergency_warn = now
                quota = self.api.volume_quota_remaining if self.api else None
                logging.warning(f"🚨 Quota emergency mode (remaining={quota}), SL only")
            return True
        return False



    def _mark_order_submitted(self):
        """Mark timestamp when order was submitted."""
        self.bot._last_order_time = time.time()



