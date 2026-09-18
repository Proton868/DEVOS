"""Model-call metrics → build_coding_progress → public coding.usage."""
from brain.coding_progress import accumulate_usage, build_coding_progress, _public_usage


def test_build_coding_progress_includes_usage():
    snap = build_coding_progress(
        mission_id="m1",
        status="agent_progress",
        usage={
            "provider": "omniroute",
            "model": "free-x",
            "input_tokens": 10,
            "output_tokens": 5,
            "latency_ms": 42,
        },
    )
    assert snap["usage"]["input_tokens"] == 10
    assert snap["usage"]["output_tokens"] == 5
    assert snap["usage"]["latency_ms"] == 42
    assert "prompt" not in str(snap).lower() or "prompt_tokens" not in snap["usage"]


def test_accumulate_sums_tokens_across_calls():
    a = accumulate_usage(None, {"input_tokens": 10, "output_tokens": 3, "latency_ms": 20, "provider": "omniroute"})
    b = accumulate_usage(a, {"input_tokens": 7, "output_tokens": 2, "latency_ms": 15, "model": "m2"})
    assert b["input_tokens"] == 17
    assert b["output_tokens"] == 5
    assert b["latency_ms"] == 35
    assert b["call_count"] == 2
    assert b["model"] == "m2"


def test_missing_metrics_not_fabricated():
    u = accumulate_usage(None, {"provider": "openrouter", "model": "free"})
    assert "input_tokens" not in u
    assert "output_tokens" not in u
    pub = _public_usage(u)
    assert pub is not None
    assert "input_tokens" not in pub


def test_fallback_and_retry_from_attempt_state():
    state = {
        "preferred_provider": "omniroute",
        "attempts": [
            {"provider": "omniroute", "model": "a", "outcome": "failed"},
            {"provider": "openrouter", "model": "b", "outcome": "success"},
        ],
    }
    u = accumulate_usage(
        None,
        {"provider": "openrouter", "model": "b", "input_tokens": 1},
        primary_provider="omniroute",
        attempt_state=state,
    )
    assert u["fallback_used"] is True
    assert u["retry_attempt"] == 1


def test_redaction_no_secrets_in_usage():
    snap = build_coding_progress(
        usage={"provider": "omniroute", "input_tokens": 1, "api_key": "sk-secret"},
    )
    # api_key must not be copied into public usage
    assert "api_key" not in (snap.get("usage") or {})
    assert "sk-secret" not in str(snap.get("usage"))


def test_sse_shaped_progress_has_coding_usage():
    """Mimic chat progress payload: coding.usage present for CodingMissionPanel."""
    usage = accumulate_usage(None, {"input_tokens": 4, "output_tokens": 2, "provider": "omniroute"})
    coding = build_coding_progress(status="agent_progress", usage=usage)
    # Chat attaches coding under prog["coding"]
    prog = {"status": "agent_progress", "coding": coding}
    assert prog["coding"]["usage"]["input_tokens"] == 4
