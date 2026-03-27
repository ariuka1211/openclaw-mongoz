"""
Tests for BotConfig: from_yaml, validate(), and defaults.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import pytest

from config import BotConfig


# ── from_yaml tests ─────────────────────────────────────────────────

class TestFromYaml:
    """BotConfig.from_yaml — parsing, env var expansion, type coercion."""

    def test_from_yaml_valid_populates_all_fields(self, tmp_path):
        """Valid YAML → all fields populated correctly."""
        cfg_file = tmp_path / "valid.yaml"
        cfg_file.write_text(
            "lighter_url: https://example.com\n"
            "account_index: 5\n"
            "api_key_index: 2\n"
            "api_key_private: my_secret\n"
            "hard_sl_pct: 2.0\n"
            "trailing_sl_trigger_pct: 0.5\n"
            "trailing_sl_step_pct: 0.95\n"
            "dsl_leverage: 20.0\n"
            "price_poll_interval: 30\n"
            "dsl_enabled: false\n"
        )
        cfg = BotConfig.from_yaml(str(cfg_file))

        assert cfg.lighter_url == "https://example.com"
        assert cfg.account_index == 5
        assert cfg.api_key_index == 2
        assert cfg.api_key_private == "my_secret"
        assert cfg.hard_sl_pct == 2.0
        assert cfg.trailing_sl_trigger_pct == 0.5
        assert cfg.trailing_sl_step_pct == 0.95
        assert cfg.dsl_leverage == 20.0
        assert cfg.price_poll_interval == 30
        assert cfg.dsl_enabled is False

    def test_from_yaml_env_var_expansion(self, tmp_path, monkeypatch):
        """Env var expansion: ${ENV_VAR} placeholders resolve."""
        monkeypatch.setenv("TEST_LIGHTER_URL", "https://env.example.com")
        monkeypatch.setenv("TEST_PRIVATE_KEY", "env_secret_key")

        cfg_file = tmp_path / "env.yaml"
        cfg_file.write_text(
            'lighter_url: "${TEST_LIGHTER_URL}"\n'
            'api_key_private: "${TEST_PRIVATE_KEY}"\n'
        )
        cfg = BotConfig.from_yaml(str(cfg_file))

        assert cfg.lighter_url == "https://env.example.com"
        assert cfg.api_key_private == "env_secret_key"

    def test_from_yaml_numeric_string_coercion(self, tmp_path, monkeypatch):
        """Type coercion: numeric strings → int/float."""
        monkeypatch.setenv("TEST_ACCT", "7")
        monkeypatch.setenv("TEST_SL", "1.75")
        monkeypatch.setenv("TEST_POLL", "15")

        cfg_file = tmp_path / "coerce.yaml"
        cfg_file.write_text(
            'account_index: "${TEST_ACCT}"\n'
            'hard_sl_pct: "${TEST_SL}"\n'
            'price_poll_interval: "${TEST_POLL}"\n'
        )
        cfg = BotConfig.from_yaml(str(cfg_file))

        assert cfg.account_index == 7
        assert isinstance(cfg.account_index, int)
        assert cfg.hard_sl_pct == 1.75
        assert isinstance(cfg.hard_sl_pct, float)
        assert cfg.price_poll_interval == 15
        assert isinstance(cfg.price_poll_interval, int)

    def test_from_yaml_dsl_enabled_string_true(self, tmp_path, monkeypatch):
        """dsl_enabled string "true" → True boolean."""
        monkeypatch.setenv("TEST_DSL", "true")
        cfg_file = tmp_path / "dsl_true.yaml"
        cfg_file.write_text('dsl_enabled: "${TEST_DSL}"\n')
        cfg = BotConfig.from_yaml(str(cfg_file))
        assert cfg.dsl_enabled is True

        monkeypatch.setenv("TEST_DSL", "True")
        cfg2 = BotConfig.from_yaml(str(cfg_file))
        assert cfg2.dsl_enabled is True

        monkeypatch.setenv("TEST_DSL", "yes")
        cfg3 = BotConfig.from_yaml(str(cfg_file))
        assert cfg3.dsl_enabled is True

        monkeypatch.setenv("TEST_DSL", "1")
        cfg4 = BotConfig.from_yaml(str(cfg_file))
        assert cfg4.dsl_enabled is True

    def test_from_yaml_unknown_keys_ignored(self, tmp_path):
        """Unknown YAML keys are silently ignored."""
        cfg_file = tmp_path / "extra.yaml"
        cfg_file.write_text(
            "lighter_url: https://x.com\n"
            "unknown_field: 42\n"
            "another_bogus: hello\n"
        )
        cfg = BotConfig.from_yaml(str(cfg_file))
        assert not hasattr(cfg, "unknown_field")
        assert not hasattr(cfg, "another_bogus")
        assert cfg.lighter_url == "https://x.com"

    def test_from_yaml_dsl_tiers_parsing(self, tmp_path):
        """dsl_tiers from YAML → list of dicts with correct types."""
        cfg_file = tmp_path / "tiers.yaml"
        cfg_file.write_text(
            "dsl_tiers:\n"
            "  - trigger_pct: 5\n"
            "    lock_hw_pct: 30\n"
            "    trailing_buffer_roe: 4\n"
            "    consecutive_breaches: 2\n"
            "  - trigger_pct: 10.0\n"
            "    lock_hw_pct: 60.0\n"
            "    trailing_buffer_roe: 2.5\n"
            "    consecutive_breaches: 1\n"
        )
        cfg = BotConfig.from_yaml(str(cfg_file))

        assert len(cfg.dsl_tiers) == 2
        assert cfg.dsl_tiers[0]["trigger_pct"] == 5
        assert cfg.dsl_tiers[0]["lock_hw_pct"] == 30
        assert cfg.dsl_tiers[0]["consecutive_breaches"] == 2
        assert cfg.dsl_tiers[1]["trigger_pct"] == 10.0
        assert cfg.dsl_tiers[1]["lock_hw_pct"] == 60.0


# ── validate() tests ─────────────────────────────────────────────────

class TestValidate:
    """BotConfig.validate() — returns list of error strings."""

    def test_validate_valid_config_no_errors(self, config):
        """Valid config → empty error list."""
        errors = config.validate()
        assert errors == []

    def test_validate_missing_lighter_url(self, config):
        """Missing lighter_url → error contains field name."""
        config.lighter_url = ""
        errors = config.validate()
        assert any("lighter_url" in e for e in errors)

    def test_validate_missing_api_key_private(self, config):
        """Missing api_key_private → error."""
        config.api_key_private = ""
        errors = config.validate()
        assert any("api_key_private" in e for e in errors)

    def test_validate_negative_account_index(self, config):
        """Negative account_index → error."""
        config.account_index = -1
        errors = config.validate()
        assert any("account_index" in e for e in errors)

    def test_validate_sl_pct_zero(self, config):
        """hard_sl_pct = 0 → error (must be positive)."""
        config.hard_sl_pct = 0
        errors = config.validate()
        assert any("hard_sl_pct" in e for e in errors)

    def test_validate_sl_pct_negative(self, config):
        """hard_sl_pct = -1 → error."""
        config.hard_sl_pct = -1
        errors = config.validate()
        assert any("hard_sl_pct" in e for e in errors)

    def test_validate_trailing_sl_trigger_pct_negative(self, config):
        """trailing_sl_trigger_pct = -1 → error."""
        config.trailing_sl_trigger_pct = -1
        errors = config.validate()
        assert any("trailing_sl_trigger_pct" in e for e in errors)

    def test_validate_trailing_sl_step_pct_zero(self, config):
        """trailing_sl_step_pct = 0 → error (must be > 0)."""
        config.trailing_sl_step_pct = 0
        errors = config.validate()
        assert any("trailing_sl_step_pct" in e for e in errors)

    def test_validate_trailing_sl_step_pct_too_high(self, config):
        """trailing_sl_step_pct = 6 → error (must be <= 5)."""
        config.trailing_sl_step_pct = 6
        errors = config.validate()
        assert any("trailing_sl_step_pct" in e for e in errors)

    def test_validate_price_poll_interval_zero(self, config):
        """price_poll_interval = 0 → error (must be >= 1)."""
        config.price_poll_interval = 0
        errors = config.validate()
        assert any("price_poll_interval" in e for e in errors)

    def test_validate_dsl_leverage_zero(self, config):
        """dsl_leverage = 0 → error (must be >= 1)."""
        config.dsl_leverage = 0
        errors = config.validate()
        assert any("dsl_leverage" in e for e in errors)

    def test_validate_dsl_enabled_empty_tiers(self, config):
        """dsl_enabled=True, dsl_tiers=[] → error (need at least one tier)."""
        config.dsl_tiers = []
        errors = config.validate()
        assert any("dsl_tiers" in e.lower() and "empty" in e.lower() for e in errors)

    def test_validate_dsl_tier_negative_trigger(self, config):
        """DSL tier with negative trigger_pct → error."""
        config.dsl_tiers = [
            {"trigger_pct": -5, "lock_hw_pct": 40, "consecutive_breaches": 3},
        ]
        errors = config.validate()
        assert any("trigger_pct" in e and "positive" in e for e in errors)

    def test_validate_dsl_tier_lock_hw_over_100(self, config):
        """DSL tier with lock_hw_pct > 100 → error."""
        config.dsl_tiers = [
            {"trigger_pct": 5, "lock_hw_pct": 150, "consecutive_breaches": 3},
        ]
        errors = config.validate()
        assert any("lock_hw_pct" in e for e in errors)

    def test_validate_dsl_tiers_not_ascending(self, config):
        """DSL tiers not in ascending order → error."""
        config.dsl_tiers = [
            {"trigger_pct": 15, "lock_hw_pct": 75, "consecutive_breaches": 2},
            {"trigger_pct": 7, "lock_hw_pct": 40, "consecutive_breaches": 3},
        ]
        errors = config.validate()
        assert any("ascending" in e.lower() or "sorted" in e.lower() or "previous" in e.lower() for e in errors)

    def test_validate_dsl_tiers_valid_no_errors(self, config):
        """Valid DSL tiers → no errors for tier-specific issues."""
        config.dsl_tiers = [
            {"trigger_pct": 5, "lock_hw_pct": 30, "trailing_buffer_roe": 4, "consecutive_breaches": 2},
            {"trigger_pct": 10, "lock_hw_pct": 60, "trailing_buffer_roe": 3, "consecutive_breaches": 1},
        ]
        errors = config.validate()
        tier_errors = [e for e in errors if "tier" in e.lower() or "trigger_pct" in e or "lock_hw_pct" in e]
        assert tier_errors == []


# ── Defaults ─────────────────────────────────────────────────────────

class TestDefaults:
    """Default values match expected."""

    def test_defaults_hard_sl_pct(self):
        assert BotConfig().hard_sl_pct == 1.25

    def test_defaults_dsl_leverage(self):
        assert BotConfig().dsl_leverage == 10.0

    def test_default_trailing_sl_trigger_pct(self):
        assert BotConfig().trailing_sl_trigger_pct == 0.5

    def test_default_trailing_sl_step_pct(self):
        assert BotConfig().trailing_sl_step_pct == 0.95

    def test_default_price_poll_interval(self):
        assert BotConfig().price_poll_interval == 60

    def test_default_price_call_delay(self):
        assert BotConfig().price_call_delay == 5.0

    def test_default_dsl_enabled(self):
        assert BotConfig().dsl_enabled is True

    def test_default_stagnation_roe_pct(self):
        assert BotConfig().stagnation_roe_pct == 8.0

    def test_default_stagnation_minutes(self):
        assert BotConfig().stagnation_minutes == 90

    def test_default_account_index(self):
        assert BotConfig().account_index == 0

    def test_default_dsl_tiers_empty(self):
        assert BotConfig().dsl_tiers == []
