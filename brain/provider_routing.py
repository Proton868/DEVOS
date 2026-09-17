"""Production-grade free-model provider routing and fallback.

Extends BrainLLM — does not replace providers.

Policy:
- OpenRouter uses OPENROUTER_DEFAULT_MODEL (openrouter/free).
- OmniRoute prefers free catalog models when free_only is set.
- 429 / transient failures rotate candidates; do not hammer the same model.
- Auth / invalid-request failures are non-retryable for that provider.
- Exhaustion is never success.
- Secrets never appear in public attempt records.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("devos.provider_routing")

# Bounded waits (seconds) after 429 / 5xx for the *same* candidate before skipping.
_RETRY_AFTER_DEFAULT = 1.0
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 30.0
_MAX_SAME_CANDIDATE_RETRIES = 2  # at most 2 same-candidate retries, then rotate


@dataclass
class FailureClass:
    category: str  # rate_limited|timeout|connection|server|auth|client|model_unavailable|policy|unknown
    retryable: bool
    http_status: Optional[int] = None
    retry_after_s: Optional[float] = None


@dataclass
class CandidateAttempt:
    provider: str
    model: str
    outcome: str  # success|failed|skipped
    category: Optional[str] = None
    http_status: Optional[int] = None
    error: Optional[str] = None  # redacted
    at: float = field(default_factory=time.time)

    def to_public_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "outcome": self.outcome,
            "category": self.category,
            "http_status": self.http_status,
            "error": self.error,
            "at": self.at,
        }


@dataclass
class ProviderAttemptState:
    """Per-request / per-mission candidate attempt tracking (durable-friendly)."""

    mission_id: Optional[str] = None
    free_only: bool = True
    attempts: list[CandidateAttempt] = field(default_factory=list)
    exhausted_labels: list[str] = field(default_factory=list)  # provider:model rate-limited
    preferred_provider: Optional[str] = None

    def label(self, provider: str, model: str) -> str:
        return f"{provider}:{model or 'default'}"

    def mark_rate_limited(self, provider: str, model: str) -> None:
        lab = self.label(provider, model)
        if lab not in self.exhausted_labels:
            self.exhausted_labels.append(lab)

    def is_exhausted(self, provider: str, model: str) -> bool:
        return self.label(provider, model) in self.exhausted_labels

    def record(self, attempt: CandidateAttempt) -> None:
        self.attempts.append(attempt)
        if attempt.category == "rate_limited":
            self.mark_rate_limited(attempt.provider, attempt.model)

    def to_public_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "free_only": self.free_only,
            "exhausted_labels": list(self.exhausted_labels),
            "preferred_provider": self.preferred_provider,
            "attempts": [a.to_public_dict() for a in self.attempts[-40:]],
            "attempt_count": len(self.attempts),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "ProviderAttemptState":
        if not data:
            return cls()
        st = cls(
            mission_id=data.get("mission_id"),
            free_only=bool(data.get("free_only", True)),
            exhausted_labels=list(data.get("exhausted_labels") or []),
            preferred_provider=data.get("preferred_provider"),
        )
        for a in data.get("attempts") or []:
            if not isinstance(a, dict):
                continue
            st.attempts.append(CandidateAttempt(
                provider=str(a.get("provider") or ""),
                model=str(a.get("model") or ""),
                outcome=str(a.get("outcome") or "failed"),
                category=a.get("category"),
                http_status=a.get("http_status"),
                error=a.get("error"),
                at=float(a.get("at") or time.time()),
            ))
        return st



def _retry_after_seconds(exc: BaseException) -> float | None:
    """Honor Retry-After header when present (seconds or HTTP-date not parsed as date)."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    headers = getattr(resp, "headers", None) or {}
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        raw = None
    if raw is None:
        return None
    try:
        return min(float(str(raw).strip()), _BACKOFF_CAP_S)
    except Exception:
        return None


def classify_provider_failure(
    exc: BaseException,
    *,
    http_status: Optional[int] = None,
) -> FailureClass:
    """Classify exceptions for routing policy (no secrets)."""
    code = http_status
    if code is None:
        resp = getattr(exc, "response", None)
        if resp is not None and getattr(resp, "status_code", None) is not None:
            try:
                code = int(resp.status_code)
            except Exception:
                code = None
        if code is None:
            text = str(exc)
            for c in (429, 503, 502, 500, 401, 403, 400, 404, 422, 408):
                if str(c) in text:
                    code = c
                    break

    retry_after = None
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            headers = getattr(resp, "headers", None) or {}
            ra = headers.get("Retry-After") or headers.get("retry-after")
            if ra is not None:
                retry_after = float(ra)
                retry_after = max(0.0, min(retry_after, 30.0))
        except Exception:
            retry_after = None

    name = type(exc).__name__
    low = str(exc).lower()

    if code == 429 or "rate limit" in low or "too many requests" in low:
        return FailureClass(
            category="rate_limited",
            retryable=True,
            http_status=429,
            retry_after_s=retry_after if retry_after is not None else _RETRY_AFTER_DEFAULT,
        )
    if code in (401, 403) or "unauthorized" in low or "forbidden" in low or "invalid api key" in low:
        return FailureClass(category="auth", retryable=False, http_status=code)
    if code in (400, 422) or "invalid request" in low or "invalid_request" in low:
        return FailureClass(category="client", retryable=False, http_status=code)
    if code == 404 or "model not found" in low or "model_not_found" in low:
        return FailureClass(category="model_unavailable", retryable=False, http_status=code or 404)
    if code == 408 or name in ("TimeoutException", "ReadTimeout", "ConnectTimeout", "TimeoutError"):
        return FailureClass(category="timeout", retryable=True, http_status=code or 408)
    if name in ("ConnectError", "NetworkError", "ProxyError") or "connection" in low:
        return FailureClass(category="connection", retryable=True, http_status=code)
    if code in (500, 502, 503, 504) or (code is not None and 500 <= code < 600):
        return FailureClass(
            category="server",
            retryable=True,
            http_status=code,
            retry_after_s=retry_after if retry_after is not None else _RETRY_AFTER_DEFAULT,
        )
    if "content" in low and ("policy" in low or "safety" in low or "refus" in low):
        return FailureClass(category="policy", retryable=False, http_status=code)
    return FailureClass(category="unknown", retryable=False, http_status=code)


def redact_error(exc: BaseException, *, max_len: int = 200) -> str:
    from brain.llm import _redact_provider_error_text
    return _redact_provider_error_text(f"{type(exc).__name__}: {exc}", max_len=max_len)


def order_providers(primary: str, available: list[str], *, prefer_omniroute: bool = True) -> list[str]:
    """Primary first, then OmniRoute (if configured), then the rest. Deduplicated."""
    out: list[str] = []
    for p in [primary]:
        if p and p not in out:
            out.append(p)
    rest = [p for p in available if p and p != primary]
    if prefer_omniroute and "omniroute" in rest:
        rest = ["omniroute"] + [p for p in rest if p != "omniroute"]
    for p in rest:
        if p not in out:
            out.append(p)
    return out


async def build_model_candidates(
    brain,
    provider: str,
    *,
    free_only: bool = True,
    state: Optional[ProviderAttemptState] = None,
) -> list[str]:
    """Ordered model ids for a provider. OpenRouter → openrouter/free only."""
    from core.config import settings

    models: list[str] = []
    if provider == "openrouter":
        models = [(settings.OPENROUTER_DEFAULT_MODEL or "openrouter/free").strip()]
    elif provider == "omniroute":
        explicit = (getattr(brain, "model", None) or settings.OMNIROUTE_DEFAULT_MODEL or "").strip()
        if explicit:
            models.append(explicit)
        try:
            listed = await brain.list_models("omniroute")
            free_ids = [m["id"] for m in (listed or []) if m.get("free") and m.get("id")]
            paid_ids = [m["id"] for m in (listed or []) if m.get("id") and not m.get("free")]
            pool = free_ids if free_only else (free_ids + paid_ids)
            if not free_only:
                # free first even when paid allowed
                pool = free_ids + [x for x in paid_ids if x not in free_ids]
            for mid in pool:
                if mid and mid not in models:
                    models.append(mid)
        except Exception as e:
            logger.debug("omniroute catalog: %s", type(e).__name__)
        if not models and explicit:
            models = [explicit]
    else:
        if getattr(brain, "model", None):
            models = [brain.model]
        else:
            models = await brain._model_candidates_for_provider(provider)

    # Drop candidates already rate-limited this mission
    if state is not None:
        models = [m for m in models if not state.is_exhausted(provider, m)]
    return models or [""]


async def route_chat_with_fallback(
    brain,
    messages: list[dict],
    *,
    allow_fallback: bool = True,
    free_only: bool = True,
    state: Optional[ProviderAttemptState] = None,
    call_fn: Optional[Callable[[str, list, str], Awaitable[str]]] = None,
) -> str:
    """
    Try providers/models with production fallback semantics.

    call_fn(provider, messages, model) -> str  (defaults to brain._call_with_model)
    Raises brain.llm.ProviderExhaustedError when all candidates fail.
    """
    from brain.llm import ProviderExhaustedError

    state = state or ProviderAttemptState(free_only=free_only)
    state.free_only = free_only
    call = call_fn or brain._call_with_model

    primary = brain.provider
    if allow_fallback:
        providers = order_providers(primary, list(brain._all_providers()), prefer_omniroute=True)
    else:
        providers = [primary]

    last_error = None
    last_fc: Optional[FailureClass] = None
    last_provider = None
    last_model = None
    tried: list[str] = []
    saw_rate_limit = False

    for provider in providers:
        models = await build_model_candidates(brain, provider, free_only=free_only, state=state)
        if not models:
            models = [""]
        for model in models:
            label = state.label(provider, model)
            if state.is_exhausted(provider, model):
                state.record(CandidateAttempt(
                    provider=provider, model=model or "default",
                    outcome="skipped", category="rate_limited",
                    error="previously_rate_limited",
                ))
                continue
            tried.append(label)

            same_retries = 0
            while same_retries <= _MAX_SAME_CANDIDATE_RETRIES:
                try:
                    result = await call(provider, messages, model)
                    state.record(CandidateAttempt(
                        provider=provider, model=model or "default", outcome="success",
                    ))
                    brain.last_error = None
                    # Attach public routing state for durability hooks
                    brain.last_provider_state = state.to_public_dict()
                    return result
                except Exception as e:
                    fc = classify_provider_failure(e)
                    last_fc = fc
                    last_error = f"{label}: {redact_error(e)}"
                    brain.last_error = last_error
                    last_provider = provider
                    last_model = model
                    if fc.category == "rate_limited":
                        saw_rate_limit = True

                    state.record(CandidateAttempt(
                        provider=provider,
                        model=model or "default",
                        outcome="failed",
                        category=fc.category,
                        http_status=fc.http_status,
                        error=redact_error(e),
                    ))

                    if fc.category == "auth":
                        # Skip remaining models for this provider
                        logger.warning("provider %s auth failure — skipping provider", provider)
                        break

                    if fc.category == "rate_limited":
                        wait = fc.retry_after_s
                        if wait is None:
                            wait = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** same_retries))
                        wait = min(float(wait), _BACKOFF_CAP_S)
                        if same_retries < _MAX_SAME_CANDIDATE_RETRIES and wait > 0:
                            same_retries += 1
                            logger.warning(
                                "rate-limited %s; backoff %.1fs (attempt %s) then retry/rotate",
                                label, wait, same_retries,
                            )
                            await asyncio.sleep(wait)
                            continue  # bounded same-candidate retry
                        state.mark_rate_limited(provider, model)
                        logger.warning("rate-limited %s; rotating to next candidate", label)
                        break

                    if fc.retryable and same_retries < _MAX_SAME_CANDIDATE_RETRIES and fc.category in (
                        "server", "timeout", "connection",
                    ):
                        same_retries += 1
                        wait = fc.retry_after_s
                        if wait is None:
                            wait = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** (same_retries - 1)))
                        await asyncio.sleep(min(float(wait), _BACKOFF_CAP_S))
                        continue

                    # Non-retryable client / model_unavailable / policy → next model
                    break
            else:
                continue
            # auth break outer models loop
            if last_fc and last_fc.category == "auth":
                break

    brain.last_provider_state = state.to_public_dict()
    raise ProviderExhaustedError(
        f"All providers failed. Last error: {last_error or 'no providers attempted'}",
        last_error=last_error,
        providers_tried=tried,
        http_status=last_fc.http_status if last_fc else None,
        retryable=(last_fc.retryable if last_fc else None) if last_fc else saw_rate_limit,
        provider=last_provider,
        model=last_model,
        category=(last_fc.category if last_fc else None) or ("rate_limited" if saw_rate_limit else None),
    )
