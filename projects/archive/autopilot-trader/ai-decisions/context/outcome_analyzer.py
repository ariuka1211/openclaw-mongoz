"""Outcome analyzer — extracts learnable patterns from trade outcomes."""

import logging
from datetime import datetime, timezone

log = logging.getLogger("ai-trader.context.outcomes")


class OutcomeAnalyzer:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader

    def analyze_and_update(self, outcomes: list[dict], history: list[dict]):
        """Analyze recent outcomes and reinforce relevant patterns.

        Called once per cycle in cycle_runner.py after outcomes are read.
        Data-driven — no LLM needed. Counts wins/losses per feature bucket,
        reinforces buckets with strong win rates.
        """
        if len(outcomes) < 3:
            return  # Not enough data to learn from

        # Match outcomes to decisions (by symbol + time window)
        decision_map = self._build_decision_map(history)

        # Extract features from each outcome
        buckets = {}  # feature_key -> {"wins": int, "losses": int}
        for o in outcomes:
            features = self._extract_features(o, decision_map)
            for feat in features:
                if feat not in buckets:
                    buckets[feat] = {"wins": 0, "losses": 0}
                if o.get("pnl_usd", 0) > 0:
                    buckets[feat]["wins"] += 1
                else:
                    buckets[feat]["losses"] += 1

        # Reinforce patterns with enough samples and strong win rate
        min_samples = 3
        win_rate_threshold = 0.6
        boost = 0.08

        reinforced = 0
        for rule, counts in buckets.items():
            total = counts["wins"] + counts["losses"]
            if total < min_samples:
                continue
            win_rate = counts["wins"] / total
            if win_rate >= win_rate_threshold:
                self.ai_trader.pattern_engine.reinforce_pattern(rule, boost=boost)
                reinforced += 1
                log.debug(f"Pattern reinforced: {rule} (win_rate={win_rate:.0%}, n={total})")

        if reinforced:
            log.info(f"Pattern update: reinforced {reinforced} rules from {len(outcomes)} outcomes")

    def _build_decision_map(self, history: list[dict]) -> dict:
        """Build symbol -> decision lookup from recent decisions."""
        return {
            h.get("symbol", ""): h
            for h in (history or [])
            if h.get("action") == "open" and h.get("executed")
        }

    def _extract_features(self, outcome: dict, decision_map: dict) -> list[str]:
        """Extract learnable feature tags from a single outcome."""
        features = []
        symbol = outcome.get("symbol", "")
        direction = outcome.get("direction", "")
        pnl = outcome.get("pnl_usd", 0)
        hold_secs = outcome.get("hold_time_seconds", 0)

        # Session bucket (Asia / EU-US overlap / US)
        ts = outcome.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            hour = dt.hour
            if 0 <= hour < 8:
                session = "asia"
            elif 8 <= hour < 16:
                session = "eu_us"
            else:
                session = "us"
            features.append(f"{direction}_{session}")
        except (ValueError, TypeError):
            pass

        # Symbol-direction
        if symbol and direction:
            features.append(f"{symbol.lower()}_{direction}")

        # Hold time buckets
        if hold_secs < 600:  # <10min
            features.append("quick_exit")
        elif hold_secs > 3600:  # >1h
            features.append("long_hold")

        # Confidence bracket (from matched decision)
        decision = decision_map.get(symbol)
        if decision:
            conf = decision.get("confidence", 0)
            if conf >= 0.7:
                features.append("high_confidence")
            elif conf < 0.4:
                features.append("low_confidence")

        return features
