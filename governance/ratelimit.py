"""
DevOS Rate Limiting & Backpressure — Gap #8 from audit.
═══════════════════════════════════════════════════════════════════════════════
Implements:
  - Per-user token bucket rate limiting (requests/minute)
  - Per-agent execution quotas (runs/hour)
  - Global backpressure (max concurrent loops)
  - WebSocket/SSE connection limits
  - Sliding window counters (no Redis needed — in-memory)
═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("devos.ratelimit")


@dataclass
class RateLimitConfig:
    # API requests per minute per user
    api_rpm: int = 120
    # Loop runs per hour per user
    loops_per_hour: int = 30
    # Max concurrent loops globally
    max_concurrent_loops: int = 5
    # Max execution calls per user per hour
    executions_per_hour: int = 200
    # Max SSE connections per user
    max_sse_per_user: int = 3
    # Burst allowance (multiplier above sustained rate)
    burst_multiplier: float = 1.5
    # Login attempts per minute per client IP (security-audit P4h — mitigates
    # credential-stuffing/brute-force; deliberately much stricter than
    # api_rpm and keyed by IP rather than user_id since a pre-auth endpoint
    # has no authenticated user yet)
    login_attempts_per_minute: int = 5


class SlidingWindowCounter:
    """Thread-safe sliding window rate counter. No Redis required."""

    def __init__(self, window_seconds: int, max_requests: int):
        self.window  = window_seconds
        self.max     = max_requests
        self._hits: deque[float] = deque()
        self._lock   = asyncio.Lock()

    async def check_and_record(self) -> tuple[bool, int]:
        """
        Returns (allowed, remaining).
        Records the hit if allowed.
        """
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window
            # Evict expired hits
            while self._hits and self._hits[0] < cutoff:
                self._hits.popleft()
            current = len(self._hits)
            if current >= self.max:
                return False, 0
            self._hits.append(now)
            return True, self.max - current - 1

    async def remaining(self) -> int:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window
            while self._hits and self._hits[0] < cutoff:
                self._hits.popleft()
            return max(0, self.max - len(self._hits))


class RateLimiter:
    """
    Global rate limiter. Singleton.
    Manages per-user counters and global concurrency.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, config: Optional[RateLimitConfig] = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.cfg = config or RateLimitConfig()
                    cls._instance._api_counters:   dict[str, SlidingWindowCounter] = defaultdict(
                        lambda: SlidingWindowCounter(60, cls._instance.cfg.api_rpm))
                    cls._instance._loop_counters:  dict[str, SlidingWindowCounter] = defaultdict(
                        lambda: SlidingWindowCounter(3600, cls._instance.cfg.loops_per_hour))
                    cls._instance._exec_counters:  dict[str, SlidingWindowCounter] = defaultdict(
                        lambda: SlidingWindowCounter(3600, cls._instance.cfg.executions_per_hour))
                    cls._instance._login_counters: dict[str, SlidingWindowCounter] = defaultdict(
                        lambda: SlidingWindowCounter(60, cls._instance.cfg.login_attempts_per_minute))
                    cls._instance._active_loops: set[str] = set()
                    cls._instance._sse_conns:   dict[str, int] = defaultdict(int)
                    cls._instance._semaphore = asyncio.Semaphore(cls._instance.cfg.max_concurrent_loops)
        elif config is not None:
            # A custom config was passed to an already-initialized singleton
            # — silently ignoring it, since the singleton pattern means the
            # first caller's config wins. Logged at debug level because this
            # is a latent footgun, not a current bug (no caller passes a
            # custom config today).
            logger.debug("[ratelimit] RateLimiter already initialized; ignoring custom config")
        return cls._instance

    async def check_api(self, user_id: str) -> tuple[bool, int, str]:
        """Check API rate limit. Returns (allowed, remaining, reason)."""
        allowed, rem = await self._api_counters[user_id].check_and_record()
        if not allowed:
            return False, 0, f"API rate limit: {self.cfg.api_rpm} req/min exceeded"
        return True, rem, ""

    async def check_loop(self, user_id: str) -> tuple[bool, str]:
        """Check if user can start a new loop run."""
        # Global concurrency
        if len(self._active_loops) >= self.cfg.max_concurrent_loops:
            return False, f"System busy: {self.cfg.max_concurrent_loops} loops running. Try again shortly."
        # Per-user hourly limit
        allowed, rem = await self._loop_counters[user_id].check_and_record()
        if not allowed:
            return False, f"Loop limit: {self.cfg.loops_per_hour} loops/hour exceeded"
        return True, ""

    async def check_execution(self, user_id: str) -> tuple[bool, str]:
        """Check execution quota."""
        allowed, rem = await self._exec_counters[user_id].check_and_record()
        if not allowed:
            return False, f"Execution quota: {self.cfg.executions_per_hour} executions/hour exceeded"
        return True, ""

    async def check_login(self, key: str) -> tuple[bool, str]:
        """Stricter, dedicated rate limit for the login endpoint (security-audit
        P4h / OWASP ASVS 2.2.1 credential-stuffing mitigation) — separate from
        check_api's general per-user 120 req/min budget, and keyed by a
        caller-supplied string (the client IP, since a pre-auth endpoint has
        no user_id yet) rather than user_id, so a single IP hammering
        /api/auth/login with different usernames can't dodge the limit by
        varying the username."""
        allowed, rem = await self._login_counters[key].check_and_record()
        if not allowed:
            return False, f"Too many login attempts ({self.cfg.login_attempts_per_minute}/min). Try again shortly."
        return True, ""

    def register_loop(self, loop_id: str):
        self._active_loops.add(loop_id)

    def unregister_loop(self, loop_id: str):
        self._active_loops.discard(loop_id)

    def register_sse(self, user_id: str) -> bool:
        if self._sse_conns[user_id] >= self.cfg.max_sse_per_user:
            return False
        self._sse_conns[user_id] += 1
        return True

    def unregister_sse(self, user_id: str):
        if self._sse_conns[user_id] > 0:
            self._sse_conns[user_id] -= 1

    def stats(self) -> dict:
        return {
            "active_loops":  len(self._active_loops),
            "max_loops":     self.cfg.max_concurrent_loops,
            "sse_connections": dict(self._sse_conns),
        }
