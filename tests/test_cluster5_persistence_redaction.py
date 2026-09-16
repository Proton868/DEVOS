"""Cluster 5: persistence boundaries scrub secrets; audit ≠ observability; provenance IDs survive."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
Path("data").mkdir(exist_ok=True)


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path}/c5.db"
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("DATABASE_URL", url)
    from core.config import settings
    from core.sync_session import dispose_sync_engine
    from governance.observability import ObservabilityStore
    from governance.audit import AuditLogger

    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "REQUIRE_POSTGRES", False)
    dispose_sync_engine()
    ObservabilityStore._instance = None
    AuditLogger._instance = None
    yield tmp_path
    ObservabilityStore._instance = None
    AuditLogger._instance = None
    dispose_sync_engine()


def test_audit_log_scrubs_details_and_keeps_provenance(iso):
    from governance.audit import get_audit_logger, AuditEventType

    audit = get_audit_logger()
    eid = audit.log(
        event_type=AuditEventType.RBAC_CHECK,
        actor_id="user-a",
        tenant_id="tenant-a",
        action="authorize",
        mission_id="m-1",
        task_id="t-1",
        outcome="allow",
        details={
            "capability": "ucip:execution.python",
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.xx",
            "api_key": "sk-proj-abcdefghijklmnopqrstuv",
            "note": "safe",
        },
        trace_id="trace-prov-1",
    )
    rows = audit.query(actor_id="user-a", limit=5)
    assert any(r["event_id"] == eid for r in rows)
    hit = next(r for r in rows if r["event_id"] == eid)
    assert hit["actor_id"] == "user-a"
    blob = json.dumps(hit)
    assert "sk-proj-abcdefghijklmnopqrstuv" not in blob
    assert "eyJhbGciOiJIUzI1NiJ9" not in blob
    assert "safe" in blob or hit["details"].get("note") == "safe"
    # provenance / correlation
    assert hit.get("details", {}).get("trace_id") == "trace-prov-1" or hit.get("trace_id") == "trace-prov-1"


def test_observability_error_scrubs_secrets(iso):
    from governance.observability import ObservabilityStore

    store = ObservabilityStore()
    entry = store.record_error(
        "http",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secretpayload",
        trace_id="tr-2",
        user_id="u-2",
        api_key="sk-proj-should-not-persist",
        note="ok-field",
    )
    assert "eyJhbGciOiJIUzI1NiJ9" not in entry["message"]
    assert "sk-proj-should-not-persist" not in json.dumps(entry)
    errors = store.list_errors(user_id="u-2")
    assert any(e["trace_id"] == "tr-2" for e in errors)


def test_evidence_save_scrubs_identity_context(iso, monkeypatch):
    from governance import evidence as ev_mod
    from governance.evidence import EvidenceChain

    evid_dir = iso / "evidence"
    evid_dir.mkdir()
    monkeypatch.setattr(ev_mod, "EVIDENCE_DIR", evid_dir)
    chain = EvidenceChain(
        goal="run task",
        identity_context={
            "actor_id": "agent-1",
            "api_key": "sk-proj-evidence-leak",
            "headers": {"Authorization": "Bearer tokensecretvalue0123456789"},
        },
    )
    chain.add_node(
        action="tool.create_file",
        actor_id="agent-1",
        metadata={"path": "index.html", "password": "hunter2"},
    )
    chain._path = evid_dir / f"{chain.chain_id}.json"
    chain.save()
    raw = chain._path.read_text()
    assert "sk-proj-evidence-leak" not in raw
    assert "hunter2" not in raw
    assert "tokensecretvalue0123456789" not in raw
    assert "agent-1" in raw
    assert "tool.create_file" in raw


def test_audit_and_observability_are_separate(iso):
    """Authorization trail must not be the same object as operational telemetry."""
    from governance.audit import get_audit_logger
    from governance.observability import ObservabilityStore

    assert get_audit_logger() is not ObservabilityStore()
    assert type(get_audit_logger()).__name__ == "AuditLogger"
    assert type(ObservabilityStore()).__name__ == "ObservabilityStore"


def test_scrub_secrets_is_canonical_for_durable_structures():
    from governance.reliability import scrub_secrets, REDACTED

    dirty = {
        "OPENAI_API_KEY": "sk-proj-abcdefghijklmnopqrstuv",
        "headers": {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.xx"},
        "ok": "keep",
    }
    clean = scrub_secrets(dirty)
    assert clean["ok"] == "keep"
    assert "REDACTED" in str(clean["OPENAI_API_KEY"])
    assert "REDACTED" in str(clean["headers"]["Authorization"])
