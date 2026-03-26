"""
Bot configuration dataclass.

Reads from YAML config with env var expansion and type coercion.
"""

import os
from dataclasses import dataclass, field

import yaml


@dataclass
class BotConfig:
    lighter_url: str = "https://mainnet.zklighter.elliot.ai"
    account_index: int = 0
    api_key_index: int = 3
    api_key_private: str = ""

    # Trailing take profit
    trailing_tp_trigger_pct: float = 3.0   # Start trailing after +3%
    trailing_tp_delta_pct: float = 1.0     # Trail by 1% from peak

    # Hard stop loss
    hard_sl_pct: float = 1.25              # Hard stop loss at -1.25% from entry

    # Telegram
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Proxy (for geo-restricted servers)
    proxy_url: str = ""

    # Polling
    price_poll_interval: int = 60
    price_call_delay: float = 5.0  # seconds between sequential get_price() calls within loops

    # AI Autopilot mode
    ai_mode: bool = False
    ai_decision_file: str = "../ipc/ai-decision.json"
    ai_result_file: str = "../ipc/ai-result.json"
    ai_trader_dir: str = "../ai-decisions"
    signals_file: str = "../ipc/signals.json"

    # DSL (Dynamic Stop Loss)
    dsl_enabled: bool = True
    default_leverage: float = 10.0
    stagnation_roe_pct: float = 8.0
    stagnation_minutes: int = 60
    dsl_tiers: list = field(default_factory=list)

    # Position management scope
    track_manual_positions: bool = False

    def validate(self) -> list[str]:
        """Validate config values. Returns list of error strings (empty = valid)."""
        errors = []

        # Required non-empty string fields
        for field_name in ("lighter_url", "api_key_private"):
            val = getattr(self, field_name)
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(f"Required field '{field_name}' is missing or empty")

        # Required integer fields (non-negative)
        for field_name in ("account_index", "api_key_index"):
            val = getattr(self, field_name)
            if not isinstance(val, int) or val < 0:
                errors.append(f"Required field '{field_name}' must be a non-negative integer")

        # Positive numbers
        if not isinstance(self.hard_sl_pct, (int, float)) or self.hard_sl_pct <= 0:
            errors.append(f"'hard_sl_pct' must be a positive number, got {self.hard_sl_pct}")

        # Non-negative (0 = immediate trigger)
        for field_name in ("trailing_tp_trigger_pct", "trailing_tp_delta_pct"):
            val = getattr(self, field_name)
            if not isinstance(val, (int, float)) or val < 0:
                errors.append(f"'{field_name}' must be a non-negative number, got {val}")

        # Minimum intervals
        if not isinstance(self.price_poll_interval, (int, float)) or self.price_poll_interval < 1:
            errors.append(f"'price_poll_interval' must be >= 1, got {self.price_poll_interval}")

        if not isinstance(self.default_leverage, (int, float)) or self.default_leverage < 1:
            errors.append(f"'default_leverage' must be >= 1, got {self.default_leverage}")

        # DSL tier validation
        if self.dsl_enabled:
            if not self.dsl_tiers:
                errors.append("'dsl_enabled' is true but 'dsl_tiers' is empty — add at least one tier")
            else:
                prev_trigger = -1
                for i, tier in enumerate(self.dsl_tiers):
                    trigger = tier.get("trigger_pct")
                    lock_hw = tier.get("lock_hw_pct")
                    breaches = tier.get("consecutive_breaches")

                    if not isinstance(trigger, (int, float)) or trigger <= 0:
                        errors.append(f"dsl_tiers[{i}].trigger_pct must be positive, got {trigger}")
                    if not isinstance(lock_hw, (int, float)) or not (0 < lock_hw <= 100):
                        errors.append(f"dsl_tiers[{i}].lock_hw_pct must be in (0, 100], got {lock_hw}")
                    if not isinstance(breaches, int) or breaches < 1:
                        errors.append(f"dsl_tiers[{i}].consecutive_breaches must be >= 1, got {breaches}")

                    buf = tier.get("trailing_buffer_roe")
                    if buf is not None and not isinstance(buf, (int, float)):
                        errors.append(f"dsl_tiers[{i}].trailing_buffer_roe must be numeric or null, got {buf!r}")

                    # Check ascending order
                    if trigger is not None and trigger <= prev_trigger:
                        errors.append(f"dsl_tiers[{i}].trigger_pct ({trigger}) must be > previous ({prev_trigger}) — tiers must be sorted ascending")
                    if trigger is not None:
                        prev_trigger = trigger

        return errors

    @classmethod
    def from_yaml(cls, path: str) -> "BotConfig":
        with open(path) as f:
            raw_text = f.read()
        # Expand ${ENV_VAR} placeholders from environment
        expanded_text = os.path.expandvars(raw_text)
        raw = yaml.safe_load(expanded_text) or {}
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in raw.items() if k in fields}
        # Coerce numeric string fields (e.g. from env var expansion)
        for key in ("account_index", "api_key_index", "price_poll_interval",
                     "stagnation_minutes", "dsl_enabled"):
            if key in filtered and isinstance(filtered[key], str):
                if key == "dsl_enabled":
                    filtered[key] = filtered[key].lower() in ("true", "1", "yes")
                else:
                    filtered[key] = int(filtered[key])
        for key in ("trailing_tp_trigger_pct", "trailing_tp_delta_pct", "hard_sl_pct",
                     "default_leverage", "stagnation_roe_pct", "price_call_delay"):
            if key in filtered and isinstance(filtered[key], str):
                filtered[key] = float(filtered[key])
        # Coerce nested dsl_tiers numeric fields (may be strings from env var expansion)
        if "dsl_tiers" in filtered and isinstance(filtered["dsl_tiers"], list):
            for tier in filtered["dsl_tiers"]:
                for tkey in ("trigger_pct", "lock_hw_pct", "consecutive_breaches"):
                    if tkey in tier and isinstance(tier[tkey], str):
                        tier[tkey] = float(tier[tkey]) if tkey != "consecutive_breaches" else int(tier[tkey])
                if "trailing_buffer_roe" in tier and isinstance(tier["trailing_buffer_roe"], str):
                    tier["trailing_buffer_roe"] = float(tier["trailing_buffer_roe"])
        return cls(**filtered)
