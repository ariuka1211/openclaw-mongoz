"""Pattern engine — learned pattern rules with decay and reinforcement."""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("ai-trader.context.patterns")


class PatternEngine:
    def __init__(self, ai_trader):
        self.ai_trader = ai_trader
        config = ai_trader.config
        _config_dir = os.path.dirname(os.path.abspath(config["_config_path"]))
        self.patterns_file = Path(_config_dir) / config.get("patterns_file", "state/patterns.json")

    def _load(self) -> dict:
        """Load patterns.json. Returns dict with 'patterns' list."""
        if not self.patterns_file.exists():
            return {"patterns": []}
        try:
            data = json.loads(self.patterns_file.read_text())
            if isinstance(data, dict) and "patterns" in data:
                return data
            return {"patterns": []}
        except (json.JSONDecodeError, OSError):
            return {"patterns": []}

    def _save(self, data: dict):
        """Write patterns.json atomically."""
        self.patterns_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.patterns_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self.patterns_file)

    def read_patterns(self) -> list[dict]:
        """Returns active patterns with confidence >= 0.4."""
        data = self._load()
        return [p for p in data.get("patterns", []) if p.get("confidence", 0) >= 0.4]

    def decay_patterns(self, decay: float = 0.02):
        """Subtract decay from each pattern confidence, drop below 0.3, write back."""
        if not self.patterns_file.exists():
            return
        try:
            data = self._load()
            original_count = len(data.get("patterns", []))
            updated = []
            for p in data.get("patterns", []):
                new_conf = p.get("confidence", 0) - decay
                if new_conf >= 0.3:
                    p["confidence"] = round(new_conf, 2)
                    updated.append(p)
            data["patterns"] = updated
            self._save(data)
            dropped = original_count - len(updated)
            if dropped > 0:
                log.info(f"Pattern decay: dropped {dropped} weak patterns, {len(updated)} remain")
        except Exception as e:
            log.warning(f"Pattern decay failed: {e}")

    def reinforce_pattern(self, rule_text: str, boost: float = 0.1):
        """Bump confidence of existing pattern or add new one at 0.5."""
        data = self._load()
        for p in data.get("patterns", []):
            if p.get("rule", "").lower() == rule_text.lower():
                p["confidence"] = min(1.0, round(p.get("confidence", 0) + boost, 2))
                self._save(data)
                return
        # New pattern
        data["patterns"].append({"rule": rule_text, "confidence": 0.5})
        self._save(data)

    def build_section(self) -> str:
        """Format active patterns (confidence >= 0.4) for prompt."""
        patterns = self.read_patterns()
        if not patterns:
            return ""
        lines = ["## Learned Patterns"]
        for p in sorted(patterns, key=lambda x: x.get("confidence", 0), reverse=True):
            lines.append(f"- {p['rule']} (confidence: {p.get('confidence', 0):.1f})")
        return "\n".join(lines)
