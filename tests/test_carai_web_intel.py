from execution.carai.provider import get_voice_provider, NullVoiceProvider, BrowserDelegatedProvider
from execution.carai.session import (
    create_voice_session, update_voice_session, append_transcript, get_transcript,
)
from execution.web_intel.safety import is_url_allowed, normalize_url
from execution.web_intel.provider import get_web_intel_provider, HttpWebIntelligenceProvider
from brain.capability_canon import canonicalize, to_ucip
from brain.personas import NUHA
from brain.delivery_intent import nuha_can_request


def test_voice_provider_health():
    p = get_voice_provider()
    h = p.health()
    assert "provider" in h
    assert h.get("telephony") is False or h.get("telephony") is None or h.get("telephony") is False


def test_voice_session_lifecycle_and_transcript():
    s = create_voice_session(user_id="t1", persona_id="nuha", project_id="p1")
    assert s["status"] == "CREATED"
    update_voice_session(s["id"], status="LISTENING")
    update_voice_session(s["id"], status="THINKING")
    append_transcript(session_id=s["id"], user_id="t1", speaker="user", text="Inspect this project.", channel="voice")
    append_transcript(session_id=s["id"], user_id="t1", speaker="nuha", text="I'll inspect the project.", channel="voice", persona_id="nuha")
    # secret redaction
    append_transcript(session_id=s["id"], user_id="t1", speaker="user", text="key sk-secret123", channel="voice")
    lines = get_transcript(session_id=s["id"])
    assert len(lines) >= 3
    assert any("REDACTED" in (l["text"] or "") for l in lines)
    update_voice_session(s["id"], status="CANCELLED")
    assert get_transcript(session_id=s["id"])


def test_ssrf_blocks_private():
    for url in ("http://127.0.0.1/", "http://localhost/", "http://192.168.1.1/", "http://169.254.169.254/latest/meta"):
        ok, reason = is_url_allowed(url)
        assert not ok, url


def test_public_url_allowed():
    ok, reason = is_url_allowed("https://example.com/path")
    assert ok and reason == "ok"


def test_web_intel_fetch_blocked_localhost():
    r = HttpWebIntelligenceProvider().fetch_public("http://127.0.0.1/")
    assert r.error and "blocked" in r.error


def test_capability_canon_voice_web():
    assert canonicalize("web.intel") == "web.intelligence"
    assert to_ucip("web.intelligence").startswith("ucip:")
    assert nuha_can_request("web.intelligence", NUHA.capabilities) or "web.intelligence" in NUHA.capabilities
    assert nuha_can_request("voice.session", NUHA.capabilities) or "voice.session" in NUHA.capabilities


def test_null_provider_fail_closed():
    n = NullVoiceProvider()
    try:
        n.transcribe(b"x")
        assert False
    except RuntimeError as e:
        assert "NOT_CONFIGURED" in str(e)
