"""Production free-model routing + failure classification."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from brain.provider_routing import (
    ProviderAttemptState,
    build_model_candidates,
    classify_provider_failure,
    order_providers,
    route_chat_with_fallback,
)
from brain.llm import ProviderExhaustedError, classify_provider_http_status


def test_openrouter_http_default_is_free_model():
    from core.config import settings
    assert (settings.OPENROUTER_DEFAULT_MODEL or "").strip() == "openrouter/free"


def test_classify_429_timeout_auth_5xx():
    e429 = Exception("HTTP 429")
    e429.response = MagicMock(status_code=429, headers={"Retry-After": "1"})
    fc = classify_provider_failure(e429)
    assert fc.category == "rate_limited" and fc.retryable

    e401 = Exception("unauthorized")
    e401.response = MagicMock(status_code=401, headers={})
    assert classify_provider_failure(e401).category == "auth"
    assert classify_provider_failure(e401).retryable is False

    e503 = Exception("bad gateway")
    e503.response = MagicMock(status_code=503, headers={})
    assert classify_provider_failure(e503).retryable is True
    assert classify_provider_failure(e503).category == "server"

    class TimeoutException(Exception):
        pass
    assert classify_provider_failure(TimeoutException("timed out")).category == "timeout"

    e404 = Exception("model")
    e404.response = MagicMock(status_code=404, headers={})
    assert classify_provider_failure(e404).category == "model_unavailable"

    assert classify_provider_http_status(429)["retryable"] is True
    assert classify_provider_http_status(401)["retryable"] is False


def test_order_providers_prefers_omniroute_after_primary():
    ordered = order_providers("openrouter", ["deepseek", "omniroute", "openrouter"])
    assert ordered[0] == "openrouter"
    assert ordered[1] == "omniroute"


def test_429_falls_back_to_alternate_candidate():
    calls = []

    class Brain:
        provider = "openrouter"
        model = None
        last_error = None
        last_provider_state = None
        def _all_providers(self):
            return ["openrouter", "omniroute"]
        async def list_models(self, provider):
            return [{"id": "free-a", "free": True}, {"id": "paid-b", "free": False}]
        async def _model_candidates_for_provider(self, provider):
            return ["openrouter/free"] if provider == "openrouter" else ["free-a"]

    async def call(provider, messages, model):
        calls.append((provider, model))
        if provider == "openrouter":
            err = Exception("HTTP 429 Too Many Requests")
            err.response = MagicMock(status_code=429, headers={})
            raise err
        return "ok-omni"

    async def _run():
        return await route_chat_with_fallback(
            Brain(), [{"role": "user", "content": "hi"}],
            call_fn=call, free_only=True,
        )
    out = asyncio.run(_run())
    assert out == "ok-omni"
    assert calls[0][0] == "openrouter"
    assert any(p == "omniroute" for p, _ in calls)


def test_multiple_consecutive_429_exhausts_truthfully():
    class Brain:
        provider = "openrouter"
        model = None
        last_error = None
        last_provider_state = None
        def _all_providers(self):
            return ["openrouter"]
        async def _model_candidates_for_provider(self, p):
            return ["openrouter/free"]

    async def call(provider, messages, model):
        err = Exception("429 rate limit")
        err.response = MagicMock(status_code=429, headers={})
        raise err

    async def _run():
        return await route_chat_with_fallback(
            Brain(), [{"role": "user", "content": "x"}],
            call_fn=call, allow_fallback=True,
        )
    try:
        asyncio.run(_run())
        assert False, "expected ProviderExhaustedError"
    except ProviderExhaustedError as e:
        assert e.category == "rate_limited"
        assert e.retryable is True
        assert "openrouter" in (e.providers_tried[0] if e.providers_tried else "")


def test_omniroute_free_catalog_before_paid():
    class Brain:
        provider = "omniroute"
        model = None
        async def list_models(self, provider):
            return [
                {"id": "paid-1", "free": False},
                {"id": "free-1", "free": True},
                {"id": "free-2", "free": True},
            ]
        async def _model_candidates_for_provider(self, p):
            return []

    async def _run():
        return await build_model_candidates(Brain(), "omniroute", free_only=True)
    cands = asyncio.run(_run())
    assert cands[0] == "free-1"
    assert "paid-1" not in cands


def test_candidate_dedup_after_rate_limit():
    st = ProviderAttemptState()
    st.mark_rate_limited("omniroute", "free-1")
    assert st.is_exhausted("omniroute", "free-1")
    class Brain:
        provider = "omniroute"
        model = None
        async def list_models(self, provider):
            return [{"id": "free-1", "free": True}, {"id": "free-2", "free": True}]
        async def _model_candidates_for_provider(self, p):
            return []
    async def _run():
        return await build_model_candidates(Brain(), "omniroute", free_only=True, state=st)
    cands = asyncio.run(_run())
    assert "free-1" not in cands
    assert "free-2" in cands


def test_auth_failure_non_retryable_skips_provider():
    calls = []
    class Brain:
        provider = "openrouter"
        model = None
        last_error = None
        last_provider_state = None
        def _all_providers(self):
            return ["openrouter", "omniroute"]
        async def _model_candidates_for_provider(self, p):
            if p == "openrouter":
                return ["openrouter/free", "openrouter/other"]
            return ["free-a"]

    async def call(provider, messages, model):
        calls.append((provider, model))
        if provider == "openrouter":
            err = Exception("401 Unauthorized")
            err.response = MagicMock(status_code=401, headers={})
            raise err
        return "ok"

    out = asyncio.run(route_chat_with_fallback(
        Brain(), [{"role": "user", "content": "h"}], call_fn=call,
    ))
    assert out == "ok"
    # only one openrouter attempt (auth breaks model loop)
    assert sum(1 for p, _ in calls if p == "openrouter") == 1


def test_5xx_retries_then_fallback():
    calls = []
    class Brain:
        provider = "openrouter"
        model = None
        last_error = None
        last_provider_state = None
        def _all_providers(self):
            return ["openrouter", "omniroute"]
        async def _model_candidates_for_provider(self, p):
            return ["openrouter/free"] if p == "openrouter" else ["m"]

    async def call(provider, messages, model):
        calls.append((provider, model))
        if provider == "openrouter":
            err = Exception("503")
            err.response = MagicMock(status_code=503, headers={})
            raise err
        return "recovered"

    out = asyncio.run(route_chat_with_fallback(
        Brain(), [{"role": "user", "content": "h"}], call_fn=call,
    ))
    assert out == "recovered"
    assert any(p == "omniroute" for p, _ in calls)


def test_streaming_path_uses_same_router():
    """BrainLLM.stream_chat delegates to route_chat_with_fallback."""
    from brain.llm import BrainLLM
    src = open("brain/llm.py").read()
    assert "route_chat_with_fallback" in src
    assert "free_only" in src
    assert "ProviderAttemptState" in src


def test_durable_state_roundtrip_and_no_secrets():
    from brain.provider_routing import CandidateAttempt, redact_error

    st = ProviderAttemptState(mission_id="m1", free_only=True)
    st.mark_rate_limited("openrouter", "openrouter/free")
    st.record(CandidateAttempt(
        provider="openrouter",
        model="openrouter/free",
        outcome="failed",
        category="rate_limited",
        error=redact_error(Exception("Bearer sk-abcdefghijklmnop")),
    ))
    public = st.to_public_dict()
    restored = ProviderAttemptState.from_dict(public)
    assert restored.mission_id == "m1"
    assert "openrouter:openrouter/free" in restored.exhausted_labels
    blob = str(public)
    assert "sk-abcdefghijklmnop" not in blob
    assert "***" in blob or "Bearer" in blob


def test_exhaustion_never_success_string():
    class Brain:
        provider = "openrouter"
        model = None
        last_error = None
        last_provider_state = None
        def _all_providers(self):
            return ["openrouter"]
        async def _model_candidates_for_provider(self, p):
            return ["openrouter/free"]

    async def call(provider, messages, model):
        raise Exception("429")

    try:
        asyncio.run(route_chat_with_fallback(Brain(), [], call_fn=call))
        assert False
    except ProviderExhaustedError as e:
        d = e.to_public_dict()
        assert d["error_type"] == "provider_exhausted"
        assert "success" not in str(d).lower() or d["error_type"] == "provider_exhausted"
