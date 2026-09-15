"""OmniRoute native gateway — deterministic unit tests (no live network)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.config import settings
from brain.llm import BrainLLM, probe_provider, system_provider_key


def test_default_provider_is_omniroute():
    assert settings.DEFAULT_PROVIDER == "omniroute"


def test_omniroute_in_available_providers():
    assert "omniroute" in settings.available_providers


def test_omniroute_base_url_default():
    assert "127.0.0.1:3000" in (settings.OMNIROUTE_BASE_URL or "")
    assert settings.OMNIROUTE_BASE_URL.rstrip("/").endswith("/api/v1")


@pytest.mark.asyncio
async def test_probe_omniroute_not_configured_when_base_empty(monkeypatch):
    monkeypatch.setattr(settings, "OMNIROUTE_BASE_URL", "")
    r = await probe_provider("omniroute")
    assert r["ok"] is False
    assert r["status"] == "NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_omniroute_call_posts_chat_completions(monkeypatch):
    monkeypatch.setattr(settings, "OMNIROUTE_BASE_URL", "http://127.0.0.1:3000/api/v1")
    monkeypatch.setattr(settings, "OMNIROUTE_DEFAULT_MODEL", "test-model")
    monkeypatch.setattr(settings, "OMNIROUTE_API_KEY", "")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "hello from omniroute"}}]
    }

    brain = BrainLLM(provider="omniroute", model="test-model")
    brain._http = MagicMock()
    brain._http.post = AsyncMock(return_value=mock_resp)

    out = await brain._call("omniroute", [{"role": "user", "content": "hi"}])
    assert out == "hello from omniroute"
    args, kwargs = brain._http.post.call_args
    assert args[0] == "http://127.0.0.1:3000/api/v1/chat/completions"
    assert kwargs["json"]["model"] == "test-model"


@pytest.mark.asyncio
async def test_omniroute_list_models_dynamic(monkeypatch):
    monkeypatch.setattr(settings, "OMNIROUTE_BASE_URL", "http://127.0.0.1:3000/api/v1")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": [
            {"id": "openrouter/free", "name": "Free"},
            {"id": "meta/llama:free", "name": "Llama Free"},
        ]
    }
    brain = BrainLLM(provider="omniroute")
    brain._http = MagicMock()
    brain._http.get = AsyncMock(return_value=mock_resp)
    models = await brain.list_models("omniroute")
    assert len(models) == 2
    assert models[0]["provider"] == "omniroute"
    assert models[1]["free"] is True


@pytest.mark.asyncio
async def test_omniroute_unavailable_raises(monkeypatch):
    import httpx
    monkeypatch.setattr(settings, "OMNIROUTE_BASE_URL", "http://127.0.0.1:3000/api/v1")
    monkeypatch.setattr(settings, "OMNIROUTE_DEFAULT_MODEL", "m1")
    brain = BrainLLM(provider="omniroute", model="m1")
    brain._http = MagicMock()
    brain._http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(RuntimeError, match="unavailable"):
        await brain._call("omniroute", [{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_omniroute_malformed_response(monkeypatch):
    monkeypatch.setattr(settings, "OMNIROUTE_BASE_URL", "http://127.0.0.1:3000/api/v1")
    monkeypatch.setattr(settings, "OMNIROUTE_DEFAULT_MODEL", "m1")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"no": "choices"}
    brain = BrainLLM(provider="omniroute", model="m1")
    brain._http = MagicMock()
    brain._http.post = AsyncMock(return_value=mock_resp)
    with pytest.raises(RuntimeError, match="Malformed"):
        await brain._call("omniroute", [{"role": "user", "content": "x"}])
