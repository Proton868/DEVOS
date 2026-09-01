"""Production reliability: secrets, idempotency, quotas, adversarial inputs."""
from governance.reliability import (
    scrub_secrets, new_idempotency_key, check_quota, reset_quota_counters,
    validate_tenant_scope, reject_authority_forgery, CorrelationChain,
)


def test_scrub_never_leaks_common_secrets():
    dirty = {
        "OPENAI_API_KEY": "sk-proj-abcdefghijklmnopqrstuv",
        "headers": {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.xx"},
        "nested": {"client_secret": "supersecret", "ok": 1},
        "note": "safe text",
    }
    clean = scrub_secrets(dirty)
    assert clean["note"] == "safe text"
    assert clean["nested"]["ok"] == 1
    assert "REDACTED" in str(clean["OPENAI_API_KEY"])
    assert "REDACTED" in str(clean["headers"]["Authorization"])
    assert "REDACTED" in str(clean["nested"]["client_secret"])


def test_idempotency_key_differs_by_operation_body():
    a = new_idempotency_key(tenant_id="t", actor_id="u", capability="c", operation="op", body={"x": 1})
    b = new_idempotency_key(tenant_id="t", actor_id="u", capability="c", operation="op", body={"x": 1})
    c = new_idempotency_key(tenant_id="t", actor_id="u", capability="c", operation="op", body={"x": 2})
    assert a == b != c


def test_quota_blocks_excess():
    reset_quota_counters()
    tid = "tenant-quota-test"
    # force low limit by hammering default max_jobs_per_hour
    for _ in range(200):
        d = check_quota(tid, "max_jobs_per_hour")
        assert d.allowed
    d = check_quota(tid, "max_jobs_per_hour")
    assert d.allowed is False


def test_tenant_scope_rejects_spoof():
    assert validate_tenant_scope("tenant-a", "tenant-a") is True
    assert validate_tenant_scope("tenant-b", "tenant-a") is False
    assert validate_tenant_scope(None, "tenant-a") is True  # omit is ok
    assert validate_tenant_scope("x", None) is False


def test_reject_authority_forgery_fields():
    hits = reject_authority_forgery({
        "goal": "do thing",
        "trust_level": "ROOT",
        "extra_caps": ["ucip:system.shell"],
        "autonomy": "full_autonomous",
    })
    assert "trust_level" in hits
    assert "extra_caps" in hits
    assert "autonomy" in hits


def test_correlation_chain_shape():
    c = CorrelationChain(tenant_id="t1", actor_id="u1", capability="ucip:execution.python")
    c.bind_job("job-1").bind_evidence("ev-1")
    d = c.to_dict()
    assert d["request_id"] and d["execution_job_id"] == "job-1"
    assert d["evidence_id"] == "ev-1"


def test_job_queue_has_lease_and_idempotency():
    src = open("workers/job_queue.py").read()
    assert "idempotency_key" in src
    assert "lease_expires_at" in src
    assert "recover_stale_leases" in src
    assert "heartbeat" in src
    assert "SKIP LOCKED" in src or "skip_locked" in src


def test_execution_job_model_reliability_columns():
    src = open("core/database.py").read()
    assert "idempotency_key" in src
    assert "lease_expires_at" in src
    assert "request_id" in src
    assert "correlation" in src
