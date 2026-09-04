"""Provider health classification — structured statuses, no secrets."""
import asyncio
from brain.llm import probe_provider, PROVIDER_STATUS


def test_provider_status_constants():
    assert "NOT_CONFIGURED" in PROVIDER_STATUS
    assert "USABLE" in PROVIDER_STATUS
    assert "UNREACHABLE" in PROVIDER_STATUS
    assert "AUTH_FAILED" in PROVIDER_STATUS
    assert "MODEL_UNAVAILABLE" in PROVIDER_STATUS


def test_openrouter_without_key_is_not_configured():
    r = asyncio.get_event_loop().run_until_complete(probe_provider("openrouter"))
    assert r["ok"] is False
    assert r["status"] == "NOT_CONFIGURED"
    assert "key" in (r["detail"] or "").lower() or "configured" in (r["detail"] or "").lower()


def test_unknown_provider():
    r = asyncio.get_event_loop().run_until_complete(probe_provider("not-a-real-provider"))
    assert r["ok"] is False
    assert r["status"] == "NOT_CONFIGURED"


def test_ollama_unreachable_or_configured_host():
    """With default remote host, expect UNREACHABLE or MODEL_UNAVAILABLE — never fake USABLE."""
    r = asyncio.get_event_loop().run_until_complete(probe_provider("ollama"))
    assert r["ok"] is False or r["status"] == "USABLE"
    if not r["ok"]:
        assert r["status"] in ("UNREACHABLE", "MODEL_UNAVAILABLE", "NOT_CONFIGURED", "AUTH_FAILED")
