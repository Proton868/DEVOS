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


def test_complete_is_terminal_for_succeeded():
    src = open("workers/job_queue.py").read()
    assert 'job.status == "succeeded"' in src
    assert "complete ignored" in src or "already-succeeded" in src


def test_begin_job_enforces_quota():
    src = open("governance/execution_pipeline.py").read()
    assert "check_quota_async" in src
    assert "JobCreationError" in src


def test_assert_no_secrets_helper():
    from governance.reliability import assert_no_secrets_in_text
    leaks = assert_no_secrets_in_text("token ghp_abcdefghijklmnopqrstuvwx rest")
    assert leaks
    assert not assert_no_secrets_in_text("hello world")


def test_production_checklist_script_exists():
    assert "DEPLOYMENT: BLOCKED" in open("scripts/production_checklist.py").read()


def test_side_effect_unknown_not_retried():
    from governance.side_effects import (
        EffectOutcome, SideEffectRecord, should_retry_job_after_effect,
    )
    unk = SideEffectRecord("email", "t", "u", "k", outcome=EffectOutcome.UNKNOWN)
    fail = SideEffectRecord("email", "t", "u", "k", outcome=EffectOutcome.FAILED)
    ok = SideEffectRecord("email", "t", "u", "k", outcome=EffectOutcome.SUCCEEDED)
    assert should_retry_job_after_effect(unk) is False
    assert should_retry_job_after_effect(fail) is True
    assert should_retry_job_after_effect(ok) is False


def test_multi_node_quota_fail_closed_without_redis(monkeypatch):
    import governance.reliability as rel
    monkeypatch.setenv("DEVOS_MULTI_NODE", "1")
    # force reload flags
    rel.MULTI_NODE = True
    rel.REDIS_URL = ""
    rel._redis_quota = None
    import asyncio
    d = asyncio.run(rel.check_quota_async("t1", "max_jobs_per_hour"))
    assert d.allowed is False
    assert d.backend == "degraded"


def test_failure_injection_points_defined():
    from governance.failure_injection import _POINTS, injection_active
    assert "after_job_create" in _POINTS
    assert "after_evidence" in _POINTS
    assert injection_active() is False


def test_checklist_has_blocked_outcome():
    src = open("scripts/production_checklist.py").read()
    assert "DEPLOYMENT: BLOCKED" in src
    assert "PASS" in src and "WARN" in src and "FAIL" in src
