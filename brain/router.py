"""
Brain — LLM Router with fallback chain, budget tracking, and latency routing.

Extends the existing BrainLLM (brain/llm.py) with:
1. **Fallback chain**: automatic provider demotion on rate-limit/failure
2. **Budget ceiling**: per-provider cost tracking and limits
3. **Latency routing**: route to fastest available provider for real-time tasks
4. **Provider priority**: weighted selection based on cost, latency, reliability
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("devos.router")


class ProviderPriority(str, Enum):
    PRIMARY   = "primary"     # Always try first
    SECONDARY = "secondary"   # Fallback if primary fails
    TERTIARY  = "tertiary"    # Last resort
    FREE      = "free"        # Free-tier only, no cost tracking


@dataclass
class ProviderStats:
    """Runtime stats for a provider, used for routing decisions."""
    provider:       str
    total_calls:    int = 0
    total_failures: int = 0
    total_latency_ms: int = 0
    total_tokens:   int = 0
    estimated_cost: float = 0.0
    last_failure:   Optional[datetime] = None
    consecutive_failures: int = 0
    cooldown_until: Optional[datetime] = None

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return 1.0 - (self.total_failures / self.total_calls)

    @property
    def avg_latency_ms(self) -> float:
        if self.total_calls == 0:
            return 0
        return self.total_latency_ms / self.total_calls

    @property
    def is_available(self) -> bool:
        if self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until:
            return False
        if self.consecutive_failures >= 3:
            return False
        return True

    def record_success(self, latency_ms: int, tokens: int):
        self.total_calls += 1
        self.total_latency_ms += latency_ms
        self.total_tokens += tokens
        self.consecutive_failures = 0
        self.cooldown_until = None

    def record_failure(self):
        self.total_calls += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_failure = datetime.now(timezone.utc)
        if self.consecutive_failures >= 3:
            cooldown = 30 * (2 ** (self.consecutive_failures - 3))  # 30s, 60s, 120s...
            self.cooldown_until = datetime.utcfromtimestamp(
                time.time() + cooldown
            )
            logger.warning(
                f"[router] {self.provider}: {self.consecutive_failures} consecutive failures, "
                f"cooldown {cooldown}s"
            )


# Estimated cost per 1K tokens (USD). These are approximate and should be
# overridden by actual pricing from the provider's API where available.
DEFAULT_COST_PER_1K: dict[str, tuple[float, float]] = {
    # (input_cost, output_cost) per 1K tokens
    "openai":       (0.0025, 0.010),
    "anthropic":    (0.003,  0.015),
    "deepseek":     (0.00014, 0.00028),
    "openrouter":   (0.001,  0.002),
    "gemini":       (0.0005, 0.0015),
    "huggingface":  (0.0,    0.0),     # free tier
    "ollama":       (0.0,    0.0),     # local
    "nararouter":   (0.001,  0.002),
}

# Provider priority chain. Free-tier providers are tried last since they
# throttle hard. The order is: quality-first (paid) → free → local fallback.
DEFAULT_PRIORITY_CHAIN: list[tuple[str, ProviderPriority]] = [
    ("anthropic",   ProviderPriority.PRIMARY),
    ("deepseek",    ProviderPriority.PRIMARY),
    ("nararouter",  ProviderPriority.PRIMARY),
    ("openrouter",  ProviderPriority.SECONDARY),
    ("gemini",      ProviderPriority.SECONDARY),
    ("huggingface", ProviderPriority.FREE),
    ("ollama",      ProviderPriority.TERTIARY),
]


class LLMRouter:
    """Enhanced LLM router with fallback chain, budget tracking, and
    latency-based routing. Wraps BrainLLM for actual calls."""

    def __init__(self, budget_limit: float = 5.0, max_latency_ms: int = 30_000):
        self._stats: dict[str, ProviderStats] = {}
        self.budget_limit = budget_limit
        self.max_latency_ms = max_latency_ms
        self._priority = dict(DEFAULT_PRIORITY_CHAIN)

    def _get_stats(self, provider: str) -> ProviderStats:
        if provider not in self._stats:
            self._stats[provider] = ProviderStats(provider=provider)
        return self._stats[provider]

    def _estimate_cost(self, provider: str, tokens: int) -> float:
        costs = DEFAULT_COST_PER_1K.get(provider, (0.001, 0.002))
        return (tokens / 1000) * costs[1]  # output cost for simplicity

    def _is_within_budget(self, provider: str) -> bool:
        stats = self._get_stats(provider)
        return stats.estimated_cost < self.budget_limit

    def _get_ordered_providers(self, preferred: Optional[str] = None,
                               require_low_latency: bool = False) -> list[str]:
        """Return providers in priority order, filtering unavailable ones."""
        available = []
        for provider, priority in DEFAULT_PRIORITY_CHAIN:
            stats = self._get_stats(provider)
            if not stats.is_available:
                continue
            if not self._is_within_budget(provider):
                continue
            if require_low_latency and stats.avg_latency_ms > self.max_latency_ms:
                continue
            available.append(provider)

        # Put preferred provider first if available
        if preferred and preferred in available:
            available.remove(preferred)
            available.insert(0, preferred)

        return available

    async def call(self, messages: list[dict], preferred: Optional[str] = None,
                   require_low_latency: bool = False,
                   max_fallbacks: int = 5) -> tuple[str, str]:
        """Call the LLM with automatic fallback. Returns (response_text, provider_used).
        Raises RuntimeError if all providers fail."""
        from brain.llm import BrainLLM

        providers = self._get_ordered_providers(
            preferred, require_low_latency
        )[:max_fallbacks + 1]

        if not providers:
            raise RuntimeError("No available LLM providers — all are in cooldown or over budget")

        last_error = None
        for provider in providers:
            stats = self._get_stats(provider)
            brain = BrainLLM(provider=provider)
            try:
                start = time.monotonic()
                response = await brain._call(provider, messages)
                elapsed_ms = int((time.monotonic() - start) * 1000)
                # Rough token estimate
                tokens = len(response) // 4
                cost = self._estimate_cost(provider, tokens)
                stats.record_success(elapsed_ms, tokens)
                stats.estimated_cost += cost
                logger.debug(
                    f"[router] {provider}: {elapsed_ms}ms, ~{tokens} tokens, "
                    f"${cost:.4f}, total ${stats.estimated_cost:.4f}"
                )
                return response, provider
            except Exception as e:
                stats.record_failure()
                last_error = e
                logger.warning(f"[router] {provider} failed: {e}")
                await brain.close()

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    async def stream_chat(self, messages: list[dict],
                          preferred: Optional[str] = None) -> str:
        """Streaming chat with fallback. Returns the full response text."""
        from brain.llm import BrainLLM

        providers = self._get_ordered_providers(preferred)[:4]
        last_error = None

        for provider in providers:
            stats = self._get_stats(provider)
            brain = BrainLLM(provider=provider)
            try:
                start = time.monotonic()
                result = await brain.stream_chat(messages)
                elapsed_ms = int((time.monotonic() - start) * 1000)
                tokens = len(result) // 4
                stats.record_success(elapsed_ms, tokens)
                stats.estimated_cost += self._estimate_cost(provider, tokens)
                return result
            except Exception as e:
                stats.record_failure()
                last_error = e
                await brain.close()

        return f"All providers failed. Last error: {last_error}"

    def get_stats(self) -> dict:
        return {
            provider: {
                "calls": s.total_calls,
                "failures": s.total_failures,
                "success_rate": round(s.success_rate, 3),
                "avg_latency_ms": round(s.avg_latency_ms, 1),
                "total_tokens": s.total_tokens,
                "estimated_cost": round(s.estimated_cost, 4),
                "available": s.is_available,
                "consecutive_failures": s.consecutive_failures,
            }
            for provider, s in self._stats.items()
        }

    def reset_budget(self):
        """Reset cost tracking for all providers."""
        for stats in self._stats.values():
            stats.estimated_cost = 0.0

    def reset_cooldowns(self):
        """Force-reset all provider cooldowns."""
        for stats in self._stats.values():
            stats.consecutive_failures = 0
            stats.cooldown_until = None


# Module-level singleton
_router = LLMRouter()


def get_router() -> LLMRouter:
    return _router


async def call_with_fallback(messages: list[dict], preferred: Optional[str] = None,
                             require_low_latency: bool = False) -> tuple[str, str]:
    """Convenience: call the LLM with automatic fallback."""
    return await _router.call(messages, preferred, require_low_latency)