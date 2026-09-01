"""Executable Production Readiness Gate v1 drills (pure-logic profile).

Live Postgres/Redis drills remain manual per docs/STAGING_DRILLS.md;
this harness proves algorithmic failure semantics with full capture.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

from chaos.capture import DrillResult, DrillReport
from chaos.provider_mock import DropResponseProvider
from governance.job_state_machine import MemQueue
from governance.side_effects import (
    execute_side_effect,
    EffectOutcome,
    should_retry_job_after_effect,
    classify_provider_result,
)
from governance.failure_injection import maybe_crash, InjectedFailure
from governance.reliability import scrub_secrets, new_idempotency_key, check_quota, reset_quota_counters


async def _run_drop_response_reconcile() -> DrillResult:
    t0 = time.perf_counter()
    provider = DropResponseProvider()
    ik = "drill-drop-1"

    async def call():
        return await provider.mutate(idempotency_key=ik, payload={"to": "a@b.c"}, drop_response=True)

    async def reconcile():
        return await provider.reconcile(ik)

    rec = await execute_side_effect(
        operation="send_email",
        tenant_id="t-drill",
        actor_id="u-drill",
        idempotency_key=ik,
        provider_idempotency_key=ik,
        call=call,
        reconcile=reconcile,
        provider="mock",
    )
    ms = (time.perf_counter() - t0) * 1000
    ok = rec.outcome == EffectOutcome.SUCCEEDED and not should_retry_job_after_effect(rec)
    return DrillResult(
        name="drop_response_external_provider",
        expected="timeout → UNKNOWN → reconcile found → SUCCEEDED, retry=false",
        actual=f"outcome={rec.outcome.value} ref={rec.external_ref} retry={should_retry_job_after_effect(rec)}",
        passed=ok,
        side_effect_outcome=rec.outcome.value,
        recovery_time_ms=ms,
        category="P0-external",
        notes="provider applied effect then dropped HTTP body",
    )


async def _run_unknown_no_reconcile() -> DrillResult:
    async def call():
        raise TimeoutError("gone")

    rec = await execute_side_effect(
        operation="charge",
        tenant_id="t",
        actor_id="u",
        idempotency_key="ik",
        provider_idempotency_key="pik",
        call=call,
        reconcile=None,
    )
    ok = rec.outcome == EffectOutcome.UNKNOWN and not should_retry_job_after_effect(rec)
    return DrillResult(
        name="unknown_blocks_blind_retry",
        expected="UNKNOWN and retry=false",
        actual=f"outcome={rec.outcome.value} retry={should_retry_job_after_effect(rec)}",
        passed=ok,
        side_effect_outcome=rec.outcome.value,
        category="P0-external",
    )


def _dual_worker_claim() -> DrillResult:
    q = MemQueue()
    q.enqueue("job-1")
    a = q.claim("worker-A")
    b = q.claim("worker-B")
    ok = a is not None and b is None and q.jobs["job-1"].worker_id == "worker-A"
    return DrillResult(
        name="two_worker_race",
        expected="exactly one owner",
        actual=f"A={getattr(a,'worker_id',None)} B={b} status={q.jobs['job-1'].status}",
        passed=ok,
        job_status=q.jobs["job-1"].status,
        execution_job_id="job-1",
        category="P0-concurrency",
    )


def _lease_expiry_stale_complete() -> DrillResult:
    q = MemQueue(lease_s=10)
    q.enqueue("job-1")
    q.claim("worker-A")
    q.jobs["job-1"].lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    second = q.claim("worker-B")
    reclaimed_by_b = second is not None and second.worker_id == "worker-B" and second.attempts == 2
    q.complete("job-1", "succeeded", result={"ok": True})
    stale = q.complete("job-1", "succeeded", result={"dup": True})
    ok = (
        reclaimed_by_b
        and q.jobs["job-1"].status == "succeeded"
        and q.jobs["job-1"].result == {"ok": True}
        and stale is False
    )
    return DrillResult(
        name="lease_expiry_second_worker",
        expected="B reclaims (attempts=2); terminal success; late complete ignored",
        actual=f"reclaimed_by_b={reclaimed_by_b} status={q.jobs['job-1'].status} result={q.jobs['job-1'].result} stale={stale}",
        passed=ok,
        job_status=q.jobs["job-1"].status,
        execution_job_id="job-1",
        category="P0-lease",
    )


def _idempotency_replay() -> DrillResult:
    q = MemQueue()
    j1 = q.enqueue("a", idempotency_key="ik-1")
    j2 = q.enqueue("b", idempotency_key="ik-1")
    ok = j1.id == j2.id
    return DrillResult(
        name="idempotency_replay",
        expected="same key → same logical job",
        actual=f"j1={j1.id} j2={j2.id}",
        passed=ok,
        execution_job_id=j1.id,
        category="P0-idempotency",
    )


def _awkward_lease_near_complete() -> DrillResult:
    """Lease expires 1s before completion race."""
    q = MemQueue(lease_s=1)
    q.enqueue("job-1")
    q.claim("worker-A")
    # expire just before complete
    q.jobs["job-1"].lease_expires_at = datetime.now(timezone.utc) - timedelta(milliseconds=1)
    b = q.claim("worker-B")
    # A still tries to complete
    q.complete("job-1", "succeeded", result={"from": "A"})
    # If B owned it, A's complete still applies to same job id — terminal
    # Second complete from anyone ignored
    ignored = q.complete("job-1", "succeeded", result={"from": "B"})
    ok = q.jobs["job-1"].status == "succeeded" and ignored is False
    return DrillResult(
        name="awkward_lease_near_complete",
        expected="terminal success; no double-success rewrite",
        actual=f"status={q.jobs['job-1'].status} result={q.jobs['job-1'].result} second={ignored}",
        passed=ok,
        job_status=q.jobs["job-1"].status,
        category="awkward-boundary",
    )


def _http_500_unknown() -> DrillResult:
    o = classify_provider_result({"http_status": 500, "request_sent": True})
    ok = o == EffectOutcome.UNKNOWN
    return DrillResult(
        name="http_500_classified_unknown",
        expected="UNKNOWN",
        actual=o.value,
        passed=ok,
        side_effect_outcome=o.value,
        category="P0-external",
    )


def _secrets_not_in_payload() -> DrillResult:
    dirty = {"api_key": "sk-abcdefghijklmnopqrstuvwxyz", "goal": "ok"}
    clean = scrub_secrets(dirty)
    ok = clean["goal"] == "ok" and "REDACTED" in str(clean["api_key"])
    return DrillResult(
        name="secrets_scrubbed_from_durable_payload",
        expected="secrets redacted",
        actual=str(clean),
        passed=ok,
        category="P0-secrets",
    )


def _inject_after_claim(monkeypatch_env: bool = True) -> DrillResult:
    import os
    os.environ["DEVOS_FAIL_INJECT"] = "after_job_claim"
    raised = False
    try:
        maybe_crash("after_job_claim")
    except InjectedFailure:
        raised = True
    finally:
        os.environ.pop("DEVOS_FAIL_INJECT", None)
    return DrillResult(
        name="failure_injection_after_job_claim",
        expected="InjectedFailure raised",
        actual=f"raised={raised}",
        passed=raised,
        category="P0-inject",
    )


async def _multi_node_quota_degraded() -> DrillResult:
    import os
    import governance.reliability as rel
    os.environ["DEVOS_MULTI_NODE"] = "1"
    rel.MULTI_NODE = True
    rel.REDIS_URL = ""
    rel._redis_quota = None
    d = await rel.check_quota_async("t-chaos", "max_jobs_per_hour")
    os.environ.pop("DEVOS_MULTI_NODE", None)
    rel.MULTI_NODE = False
    ok = d.allowed is False and d.backend == "degraded"
    return DrillResult(
        name="multi_node_redis_down_fail_closed",
        expected="expensive quota fail closed",
        actual=f"allowed={d.allowed} backend={d.backend} reason={d.reason}",
        passed=ok,
        category="P1-redis",
    )


def _max_attempts_terminal() -> DrillResult:
    q = MemQueue()
    q.enqueue("job-1")
    for w in ("w1", "w2", "w3"):
        j = q.claim(w)
        assert j
        q.complete("job-1", "failed", error="x")
    ok = q.jobs["job-1"].status == "failed" and q.claim("w4") is None
    return DrillResult(
        name="max_attempts_then_terminal_failed",
        expected="failed after max attempts; no further claim",
        actual=f"status={q.jobs['job-1'].status} attempts={q.jobs['job-1'].attempts}",
        passed=ok,
        job_status=q.jobs["job-1"].status,
        category="P0-lease",
    )


async def run_all_drills(profile: str = "pure-logic") -> DrillReport:
    report = DrillReport(profile=profile)
    report.add(await _run_drop_response_reconcile())
    report.add(await _run_unknown_no_reconcile())
    report.add(_dual_worker_claim())
    report.add(_lease_expiry_stale_complete())
    report.add(_idempotency_replay())
    report.add(_awkward_lease_near_complete())
    report.add(_http_500_unknown())
    report.add(_secrets_not_in_payload())
    report.add(_inject_after_claim())
    report.add(await _multi_node_quota_degraded())
    report.add(_max_attempts_terminal())
    return report
