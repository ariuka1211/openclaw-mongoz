"""Tests for context/token_estimator.py — estimate_tokens() and MAX_PROMPT_TOKENS"""
import pytest

from context.token_estimator import estimate_tokens, MAX_PROMPT_TOKENS


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_known_string_reasonable_estimate(self):
        # "Hello world" — with cl100k_base this should be ~2 tokens
        # With char//4 fallback it would be 11//4 = 2
        result = estimate_tokens("Hello world")
        assert 1 <= result <= 4

    def test_long_string_proportional(self):
        # 400-char string should produce roughly proportional tokens
        text = "a" * 400
        result = estimate_tokens(text)
        assert result > 0
        # Whether tiktoken or fallback, should be in a reasonable range
        assert result >= 50  # at least 50 tokens for 400 chars


class TestMaxPromptTokens:
    def test_max_prompt_tokens_value(self):
        assert MAX_PROMPT_TOKENS == 16000
