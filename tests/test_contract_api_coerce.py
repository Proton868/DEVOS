"""Repository API accepts dict positional and keyword fields."""
from __future__ import annotations

from execution.durable_store import _coerce_fields, upsert_runtime, save_share, read_logs
from execution.web_intel.store import emit_event, upsert_page, claim_queued_pages
from governance.audit import AuditEventType, AuditEntry, get_audit_logger


def test_coerce_fields_dict_and_kwargs():
    assert _coerce_fields({"a": 1}, b=2) == {"a": 1, "b": 2}
    assert _coerce_fields(x=1) == {"x": 1}


def test_emit_event_dual_signature():
    emit_event("c1", "crawl.failed", {"reason": "x"})
    emit_event("c2", {"type": "crawl.failed", "reason": "y"})


def test_upsert_page_dict():
    pid = upsert_page({"url": "https://example.com", "page_id": "p1"})
    assert pid == "p1"


def test_claim_queued_pages_crawl_and_limit():
    # Signature accepts crawl_id + limit; empty crawl yields empty claim
    assert claim_queued_pages("crawl-no-such", limit=2) == []


def test_audit_event_types_and_entry():
    assert AuditEventType.RBAC_CHECK.value == "rbac.check"
    assert AuditEventType.CAPABILITY_INVOKE.value == "capability.invoke"
    d = AuditEntry(
        event_id="e1",
        event_type=AuditEventType.CAPABILITY_INVOKE,
        actor_id="u",
        details={"n": 1},
    ).to_dict()
    assert d["event_type"] == "capability.invoke"
    assert d["event_id"] == "e1"


def test_upsert_runtime_accepts_dict_signature():
    """Contract: single dict positional must not raise TypeError at call boundary."""
    import inspect
    sig = inspect.signature(upsert_runtime)
    # *args must be present so dict positional is valid
    assert any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values())


def test_read_logs_accepts_after_id():
    assert read_logs("rt1", after_id=0, limit=10) == []
