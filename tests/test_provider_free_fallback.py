"""OpenRouter free model + 429 → multi-provider/model fallback."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


def test_openrouter_default_model_is_free():
    src = (ROOT / "core" / "config.py").read_text()
    assert 'OPENROUTER_DEFAULT_MODEL: str = "openrouter/free"' in src
    llm = (ROOT / "brain" / "llm.py").read_text()
    assert "OPENROUTER_DEFAULT_MODEL" in llm
    assert "openrouter/free" in llm
    assert "_model_candidates_for_provider" in llm
    routing = (ROOT / "brain" / "provider_routing.py").read_text()
    assert "rate_limited" in routing
    assert "route_chat_with_fallback" in llm or "route_chat_with_fallback" in routing


def test_classify_429_retryable_isolated():
    src = (ROOT / "brain" / "llm.py").read_text()
    start = src.index("def classify_provider_http_status")
    end = src.index("\ndef _redact_provider_error_text", start)
    ns: dict = {}
    exec(src[start:end], ns, ns)
    c = ns["classify_provider_http_status"](429)
    assert c["retryable"] is True
    assert c["category"] == "rate_limited"
    assert ns["classify_provider_http_status"](401)["retryable"] is False


def test_stream_chat_429_falls_back_to_next_provider():
    from brain.llm import BrainLLM

    brain = BrainLLM.__new__(BrainLLM)
    brain.provider = "openrouter"
    brain.model = None
    brain.user_id = None
    brain.last_error = None
    calls: list[tuple[str, str]] = []

    async def fake_call_with_model(provider, messages, model):
        calls.append((provider, model or ""))
        if provider == "openrouter":
            err = Exception("HTTP 429 Too Many Requests")
            err.response = MagicMock(status_code=429)
            raise err
        if provider == "omniroute":
            return "ok-from-omniroute"
        raise Exception("skip")

    async def fake_models(provider):
        if provider == "openrouter":
            return ["openrouter/free"]
        if provider == "omniroute":
            return ["free-model-a"]
        return [""]

    brain._call_with_model = fake_call_with_model
    brain._model_candidates_for_provider = fake_models
    brain._all_providers = lambda: ["openrouter", "omniroute"]

    out = asyncio.run(
        brain.stream_chat([{"role": "user", "content": "hi"}], allow_fallback=True)
    )
    assert out == "ok-from-omniroute"
    assert any(p == "openrouter" for p, _ in calls)
    assert any(p == "omniroute" for p, _ in calls)


def test_stream_chat_all_rate_limited_raises_exhausted_retryable():
    from brain.llm import BrainLLM, ProviderExhaustedError

    brain = BrainLLM.__new__(BrainLLM)
    brain.provider = "openrouter"
    brain.model = None
    brain.user_id = None
    brain.last_error = None

    async def always_429(provider, messages, model):
        err = Exception("429 rate limit")
        err.response = MagicMock(status_code=429)
        raise err

    async def models(provider):
        return ["openrouter/free"] if provider == "openrouter" else ["m1"]

    brain._call_with_model = always_429
    brain._model_candidates_for_provider = models
    brain._all_providers = lambda: ["openrouter", "omniroute"]

    try:
        asyncio.run(
            brain.stream_chat([{"role": "user", "content": "x"}], allow_fallback=True)
        )
        assert False, "expected ProviderExhaustedError"
    except ProviderExhaustedError as err:
        assert err.retryable is True or err.category == "rate_limited"
        assert err.http_status == 429 or (err.last_error and "429" in err.last_error)
        assert len(err.providers_tried) >= 2


def test_omniroute_model_candidates_prefer_free():
    from brain.llm import BrainLLM

    brain = BrainLLM.__new__(BrainLLM)
    brain.provider = "omniroute"
    brain.model = None
    brain.user_id = None

    async def list_models(provider):
        return [
            {"id": "paid-big", "free": False},
            {"id": "vendor/free-small", "free": True},
            {"id": "vendor/free-other", "free": True},
        ]

    brain.list_models = list_models
    with patch("brain.llm.settings") as st:
        st.OMNIROUTE_DEFAULT_MODEL = ""
        st.OPENROUTER_DEFAULT_MODEL = "openrouter/free"
        cands = asyncio.run(
            brain._model_candidates_for_provider("omniroute")
        )
    assert cands[0] == "vendor/free-small"
    assert "vendor/free-other" in cands
    assert "paid-big" in cands


def test_openrouter_candidates_only_free_default():
    from brain.llm import BrainLLM

    brain = BrainLLM.__new__(BrainLLM)
    brain.provider = "openrouter"
    brain.model = "some/paid-model"
    brain.user_id = None
    with patch("brain.llm.settings") as st:
        st.OPENROUTER_DEFAULT_MODEL = "openrouter/free"
        cands = asyncio.run(
            brain._model_candidates_for_provider("openrouter")
        )
    assert cands == ["openrouter/free"]


def test_no_fake_runtime_in_llm():
    src = (ROOT / "brain" / "llm.py").read_text()
    assert "DEVOS_ORCH_FAKE_RUNTIME" not in src
