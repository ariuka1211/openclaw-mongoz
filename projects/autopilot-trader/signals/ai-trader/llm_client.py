"""
OpenRouter LLM client with retry, fallback, cost tracking.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger("ai-trader.llm")


@dataclass
class LLMStats:
    total_calls: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost: float = 0.0
    fallback_count: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    _latency_samples: list = field(default_factory=list)

    # Approximate cost rates (USD per 1M tokens) — conservative mid-range estimate
    # Covers typical OpenRouter models via Kilo gateway; not exact but directionally useful
    COST_PER_1M_INPUT = 0.50
    COST_PER_1M_OUTPUT = 1.50

    def record(self, tokens_in: int, tokens_out: int, latency_ms: int):
        self.total_calls += 1
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.total_cost += (
            tokens_in * self.COST_PER_1M_INPUT / 1_000_000
            + tokens_out * self.COST_PER_1M_OUTPUT / 1_000_000
        )
        self._latency_samples.append(latency_ms)
        # Rolling average over last 100
        self._latency_samples = self._latency_samples[-100:]
        self.avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)

    def to_dict(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost": round(self.total_cost, 4),
            "fallback_count": self.fallback_count,
            "error_count": self.error_count,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
        }


class LLMClient:
    def __init__(self, config: dict):
        self.api_base = config["api_base"]
        self.primary_model = config["primary_model"]
        self.fallback_model = config["fallback_model"]
        self.timeout_seconds = config.get("timeout_seconds", 30)
        self.max_retries = config.get("max_retries", 2)
        self.api_key = os.environ.get("KILOCODE_API_KEY", "")
        self.stats = LLMStats()
        self._session: aiohttp.ClientSession | None = None

        if not self.api_key:
            raise SystemExit("KILOCODE_API_KEY is not set — cannot start AI trader")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return a reusable aiohttp session, creating it on first use."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """Call LLM with retry + fallback. Returns raw text response."""
        model = model or self.primary_model
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._call_once(
                    model, system_prompt, user_prompt, temperature, max_tokens
                )
            except asyncio.TimeoutError:
                last_error = f"Timeout ({self.timeout_seconds}s)"
                log.warning(f"LLM timeout attempt {attempt}/{self.max_retries} ({model})")
            except aiohttp.ClientResponseError as e:
                last_error = f"HTTP {e.status}: {e.message}"
                log.warning(f"LLM HTTP error attempt {attempt}/{self.max_retries}: {e.status}")
                if e.status == 429:
                    # Rate limit — backoff
                    await asyncio.sleep(60)
            except Exception as e:
                last_error = str(e)
                log.warning(f"LLM error attempt {attempt}/{self.max_retries}: {e}")

            await asyncio.sleep(min(2 ** (attempt + 1), 30))  # Exponential backoff: 4s, 8s, 16s, 30s max

        # All retries failed — try fallback
        if model != self.fallback_model:
            log.warning(f"All retries failed for {model}, trying fallback {self.fallback_model}")
            self.stats.fallback_count += 1
            try:
                return await self._call_once(
                    self.fallback_model, system_prompt, user_prompt, temperature, max_tokens
                )
            except Exception as e:
                last_error = str(e)
                log.error(f"Fallback also failed: {e}")

        self.stats.error_count += 1
        raise RuntimeError(f"LLM call failed after all retries: {last_error}")

    async def _call_once(
        self, model: str, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int
    ) -> str:
        """Single LLM API call."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://signals.local",
            "X-Title": "AI Trader",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        t0 = time.time()

        session = await self._get_session()
        async with session.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        latency_ms = int((time.time() - t0) * 1000)

        # Validate response structure
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"Empty choices in LLM response: {data}")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not content:
            raise RuntimeError(f"No content in LLM response: {choices[0]}")

        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)

        self.stats.record(tokens_in, tokens_out, latency_ms)
        log.info(f"LLM {model}: {tokens_in}→{tokens_out} tokens, {latency_ms}ms")

        return content

    async def call_with_model(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """Call a specific model (used for reflection with cheaper model)."""
        return await self.call(
            system_prompt, user_prompt, model=model, temperature=temperature, max_tokens=max_tokens
        )

    async def close(self):
        """Close the reusable aiohttp session to prevent resource leaks."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
