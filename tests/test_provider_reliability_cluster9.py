"""Cluster 9: provider/model execution reliability."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from brain.llm import (
    BrainLLM,
    ProviderExhaustedError,
    _redact_provider_error_text,
    resolve_chat_provider,
)
from core.config import settings


def test_resolve_chat_provider_default_omniroute(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_PROVIDER", "omniroute")
    assert resolve_chat_provider(None, "") == "omniroute"
    assert resolve_chat_provider("openrouter") == "openrouter"


def test_redact_bearer_and_api_key():
    raw = "Authorization Bearer sk-abcdefghijklmnopqrstuvwxyz error api_key=secretvalue123456"
    out = _redact_provider_error_text(raw)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in out
    assert "Bearer ***" in out or "bearer ***" in out.lower() or "sk-***" in out


def test_parse_unparseable_does_not_mark_complete():
    brain = BrainLLM(provider="omniroute", model="m")
    assert brain._parse("this is not json at all") is None
    assert brain._parse('{"action": "ok"}')["action"] == "ok"


@pytest.mark.asyncio
async def test_empty_provider_content_raises(monkeypatch):
    monkeypatch.setattr(settings, "OMNIROUTE_BASE_URL", "http://127.0.0.1:3000/api/v1")
    monkeypatch.setattr(settings, "OMNIROUTE_DEFAULT_MODEL", "m1")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": "  "}}]}
    brain = BrainLLM(provider="omniroute", model="m1")
    brain._http = MagicMock()
    brain._http.post = AsyncMock(return_value=mock_resp)
    with pytest.raises(RuntimeError, match="Empty provider response"):
        await brain._call("omniroute", [{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_stream_chat_raises_on_total_failure(monkeypatch):
    monkeypatch.setattr(settings, "OMNIROUTE_BASE_URL", "http://127.0.0.1:3000/api/v1")
    monkeypatch.setattr(settings, "OMNIROUTE_DEFAULT_MODEL", "m1")
    brain = BrainLLM(provider="omniroute", model="m1")
    brain._http = MagicMock()
    brain._http.post = AsyncMock(side_effect=httpx.ConnectError("down"))
    # Prevent long fallback list
    brain._all_providers = lambda: ["omniroute"]
    with pytest.raises(ProviderExhaustedError) as ei:
        await brain.stream_chat([{"role": "user", "content": "hi"}], allow_fallback=False)
    assert "omniroute" in (ei.value.providers_tried or [])


@pytest.mark.asyncio
async def test_stream_chat_success_no_false_failure(monkeypatch):
    monkeypatch.setattr(settings, "OMNIROUTE_BASE_URL", "http://127.0.0.1:3000/api/v1")
    monkeypatch.setattr(settings, "OMNIROUTE_DEFAULT_MODEL", "m1")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "ok"}}]
    }
    brain = BrainLLM(provider="omniroute", model="m1")
    brain._http = MagicMock()
    brain._http.post = AsyncMock(return_value=mock_resp)
    out = await brain.stream_chat([{"role": "user", "content": "hi"}], allow_fallback=False)
    assert out == "ok"


def test_provider_exhausted_is_not_success_message():
    err = ProviderExhaustedError("All providers failed", last_error="x", providers_tried=["omniroute"])
    assert isinstance(err, RuntimeError)
    assert "All providers failed" in str(err)
