"""Failure injection & exactly-once logical semantics drills (no live DB)."""
from datetime import datetime, timezone, timedelta
import asyncio

from governance.job_state_machine import MemQueue
from governance.side_effects import (
    execute_side_effect, EffectOutcome, should_retry_job_after_effect, SideEffectRecord,
)
from governance.failure_injection import InjectedFailure, maybe_crash


def test_dual_worker_claim_exactly_one_owner():
    q = MemQueue()
    q.enqueue("job-1")
    a = q.claim("worker-A")
    b = q.claim("worker-B")
    assert a is not None and a.worker_id == "worker-A"
    assert b is None  # only one queued job
    assert q.jobs["job-1"].status == "running"
    assert q.jobs["job-1"].worker_id == "worker-A"


def test_lease_expiry_allows_second_worker_without_duplicate_success():
    q = MemQueue(lease_s=10)
    q.enqueue("job-1")
    first = q.claim("worker-A")
    assert first is not None
    # Simulate crash: lease expires, no complete
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    q.jobs["job-1"].lease_expires_at = past
    second = q.claim("worker-B")
    assert second is not None
    assert second.worker_id == "worker-B"
    assert second.attempts == 2
    # First worker's late complete must not win if already re-queued path:
    # worker-B completes successfully
    assert q.complete("job-1", "succeeded", result={"ok": True}) is True
    assert q.jobs["job-1"].status == "succeeded"
    # Stale complete from worker-A ignored
    assert q.complete("job-1", "succeeded", result={"dup": True}) is False
    assert q.jobs["job-1"].result == {"ok": True}


def test_idempotent_enqueue_same_key():
    q = MemQueue()
    j1 = q.enqueue("a", idempotency_key="ik-1")
    j2 = q.enqueue("b", idempotency_key="ik-1")
    assert j1.id == j2.id == "a"


def test_crash_after_external_success_becomes_unknown_not_blind_retry():
    async def provider_timeout():
        raise TimeoutError("provider hung after accept")

    async def reconcile_found():
        return {"id": "ext-123", "status": "success"}

    rec = asyncio.run(
        execute_side_effect(
            operation="send_email",
            tenant_id="t",
            actor_id="u",
            idempotency_key="ik",
            provider_idempotency_key="pik",
            call=provider_timeout,
            reconcile=reconcile_found,
            provider="smtp",
        )
    )
    assert rec.outcome == EffectOutcome.SUCCEEDED  # reconciled
    assert rec.external_ref == "ext-123"
    assert should_retry_job_after_effect(rec) is False


def test_unknown_without_reconcile_blocks_retry():
    async def provider_timeout():
        raise TimeoutError("gone")

    rec = asyncio.run(
        execute_side_effect(
            operation="charge",
            tenant_id="t",
            actor_id="u",
            idempotency_key="ik",
            provider_idempotency_key="pik",
            call=provider_timeout,
            reconcile=None,
            provider="stripe",
        )
    )
    assert rec.outcome == EffectOutcome.UNKNOWN
    assert should_retry_job_after_effect(rec) is False


def test_failed_before_send_may_retry():
    async def local_validation():
        raise ValueError("validation failed before send")

    rec = asyncio.run(
        execute_side_effect(
            operation="charge",
            tenant_id="t",
            actor_id="u",
            idempotency_key="ik",
            provider_idempotency_key=None,
            call=local_validation,
        )
    )
    assert rec.outcome == EffectOutcome.FAILED
    assert should_retry_job_after_effect(rec) is True


def test_inject_point_raises_when_enabled(monkeypatch):
    monkeypatch.setenv("DEVOS_FAIL_INJECT", "after_job_create")
    # re-import enabled set by calling maybe_crash
    try:
        maybe_crash("after_job_create")
        assert False, "expected InjectedFailure"
    except InjectedFailure:
        pass
    maybe_crash("after_evidence")  # not enabled — no raise


def test_failed_retries_until_max_then_terminal():
    q = MemQueue()
    q.enqueue("job-1")
    for i, w in enumerate(["w1", "w2", "w3"]):
        j = q.claim(w)
        assert j is not None
        q.complete("job-1", "failed", error=f"err{i}")
    assert q.jobs["job-1"].status == "failed"
    assert q.jobs["job-1"].attempts == 3
    assert q.claim("w4") is None
