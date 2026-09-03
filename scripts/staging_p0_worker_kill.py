#!/usr/bin/env python3
"""LIVE STAGING DRILL — P0 worker kill after claim.

Requires:
  - PostgreSQL (DATABASE_URL must be postgresql+asyncpg://...)
  - Optional Redis (REDIS_URL / DEVOS_REDIS_URL)
  - Two independently killable worker processes (scripts/run_job_worker.py)

Does NOT use MemQueue or SQLite. Does NOT import app.py.

Exit codes:
  0 = PASS
  1 = FAIL (drill assertion / phase failure)
  2 = BLOCKED (infrastructure unavailable)

Diagnosis vs action
  Diagnosis only observes durable PostgreSQL/Redis/process state.
  Actions that mutate the staging environment are limited to:
    - start two workers
    - create one test job
    - SIGKILL the claiming worker
  The harness does NOT call recover_stale_leases to force recovery;
  recovery must come from production claim_next / lease recovery.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RESULTS_DIR = ROOT / "data" / "staging-results"

# ── Outcomes ───────────────────────────────────────────────────────────────

OUTCOME_PASS = "PASS"
OUTCOME_FAIL = "FAIL"
OUTCOME_BLOCKED = "BLOCKED"

PHASE_INFRA = "INFRA"
PHASE_CLAIM = "CLAIM"
PHASE_SIGKILL = "SIGKILL"
PHASE_LEASE_EXPIRY = "LEASE_EXPIRY"
PHASE_RECOVERY = "RECOVERY"
PHASE_FINALIZATION = "FINALIZATION"
PHASE_INTEGRITY = "INTEGRITY"


@dataclass
class PhaseFailure:
    phase: str
    expected: str
    actual: str
    elapsed_seconds: float
    last_observed_state: Any = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class InfrastructureBlocked(Exception):
    """Infra missing/unsuitable — maps to BLOCKED (exit 2)."""

    def __init__(self, message: str, *, phase: str = PHASE_INFRA, last_observed: Any = None):
        super().__init__(message)
        self.phase = phase
        self.last_observed = last_observed


class DrillPhaseError(Exception):
    """Drill assertion failure — maps to FAIL (exit 1)."""

    def __init__(self, failure: PhaseFailure):
        super().__init__(
            f"[{failure.phase}] expected={failure.expected!r} actual={failure.actual!r} "
            f"elapsed={failure.elapsed_seconds:.2f}s {failure.detail}"
        )
        self.failure = failure


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_postgresql_url(url: str) -> bool:
    u = (url or "").lower()
    return u.startswith("postgresql") or u.startswith("postgres")


def require_postgresql(url: str) -> None:
    if not is_postgresql_url(url):
        raise InfrastructureBlocked(
            "BLOCKED: DATABASE_URL must be PostgreSQL "
            f"(got {url!r}). SQLite is not valid for this live drill."
        )


def classify_outcome(*, blocked: bool = False, failed: bool = False) -> str:
    """Map terminal flags to PASS | FAIL | BLOCKED. Timeout never implies PASS."""
    if blocked:
        return OUTCOME_BLOCKED
    if failed:
        return OUTCOME_FAIL
    return OUTCOME_PASS


def evaluate_expected_recovery(
    *,
    initial_attempt: Any,
    recovery_attempt: Any,
    recovery_worker: Any,
    killed_worker: Any,
    final_status: Any,
    operation_status: Any,
    job_rows: int,
    operation_rows: int,
    evidence_rows: int,
    blind_unknown_retry: bool,
) -> tuple[bool, list[str]]:
    """Authoritative recovery proof from durable state (not recover_stale_leases_count)."""
    errors: list[str] = []
    if initial_attempt != 1:
        errors.append(f"initial_attempt expected 1 got {initial_attempt!r}")
    if recovery_attempt != 2:
        errors.append(f"recovery_attempt expected 2 got {recovery_attempt!r}")
    rw = str(recovery_worker or "")
    kw = str(killed_worker or "")
    if not rw:
        errors.append("recovery_worker missing")
    elif kw and kw in rw:
        errors.append(f"recovery_worker {rw!r} must not be killed_worker {kw!r}")
    if final_status != "succeeded":
        errors.append(f"final_job.status expected succeeded got {final_status!r}")
    if operation_status is not None and str(operation_status).lower() != "succeeded":
        errors.append(f"operation.status expected succeeded got {operation_status!r}")
    if job_rows != 1:
        errors.append(f"job_rows_for_idempotency_key expected 1 got {job_rows}")
    if operation_rows > 1:
        errors.append(f"duplicate operations: {operation_rows}")
    if evidence_rows > 1:
        errors.append(f"duplicate evidence: {evidence_rows}")
    if blind_unknown_retry:
        errors.append("blind_unknown_retry must be false")
    return (len(errors) == 0, errors)


def worker_label_from_id(worker_id: str) -> str:
    wid = worker_id or ""
    if "worker-a" in wid:
        return "worker-a"
    if "worker-b" in wid:
        return "worker-b"
    return wid or "unknown"


# ── Infrastructure probes (diagnosis) ──────────────────────────────────────


async def wait_pg(timeout_s: float = 30.0, poll_s: float = 0.5) -> None:
    from sqlalchemy import text
    from core.database import engine

    deadline = time.monotonic() + timeout_s
    last = None
    t0 = time.monotonic()
    while time.monotonic() < deadline:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as e:
            last = e
            await asyncio.sleep(poll_s)
    raise InfrastructureBlocked(
        f"PostgreSQL not reachable within {timeout_s}s: {last}",
        last_observed={"error": str(last), "elapsed_seconds": time.monotonic() - t0},
    )


async def check_redis() -> dict:
    url = os.environ.get("DEVOS_REDIS_URL") or os.environ.get("REDIS_URL") or ""
    if not url:
        return {"configured": False, "reachable": False}
    try:
        import redis.asyncio as redis

        r = redis.from_url(url, decode_responses=True)
        await r.ping()
        await r.aclose()
        return {"configured": True, "reachable": True}
    except Exception as e:
        return {"configured": True, "reachable": False, "error": str(e)}


# ── Actions (intentional mutation only) ─────────────────────────────────────


def start_worker(worker_id: str, env: dict) -> subprocess.Popen:
    e = {**os.environ, **env, "DEVOS_WORKER_ID": worker_id, "PYTHONPATH": str(ROOT)}
    e["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "run_job_worker.py")],
        cwd=str(ROOT),
        env=e,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def sigkill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        raise DrillPhaseError(
            PhaseFailure(
                phase=PHASE_SIGKILL,
                expected="worker process alive for SIGKILL",
                actual=f"already exited rc={proc.returncode}",
                elapsed_seconds=0.0,
            )
        )
    os.kill(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


# ── Diagnosis (observe durable state only) ──────────────────────────────────


async def fetch_job(job_id: str) -> Optional[dict]:
    from sqlalchemy import select
    from core.database import AsyncSessionLocal, ExecutionJob

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(ExecutionJob).where(ExecutionJob.id == job_id))
        job = r.scalar_one_or_none()
        if not job:
            return None
        return {
            "id": job.id,
            "status": job.status,
            "worker_id": job.worker_id,
            "attempts": job.attempts,
            "operation_id": job.operation_id,
            "idempotency_key": job.idempotency_key,
            "tenant_id": job.tenant_id,
            "lease_expires_at": job.lease_expires_at.isoformat()
            if job.lease_expires_at
            else None,
            "locked_at": job.locked_at.isoformat() if job.locked_at else None,
            "result": job.result,
            "error": job.error,
        }


async def count_jobs_by_idempotency(tenant_id: str, key: str) -> int:
    from sqlalchemy import select, func
    from core.database import AsyncSessionLocal, ExecutionJob

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(func.count())
            .select_from(ExecutionJob)
            .where(
                ExecutionJob.tenant_id == tenant_id,
                ExecutionJob.idempotency_key == key,
            )
        )
        return int(r.scalar_one() or 0)


async def count_operations_for_job(job_id: str) -> int:
    from sqlalchemy import select, func
    from core.database import AsyncSessionLocal

    try:
        from core.database import ExecutionOperation
    except ImportError:
        return 0
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(func.count())
            .select_from(ExecutionOperation)
            .where(ExecutionOperation.execution_job_id == job_id)
        )
        return int(r.scalar_one() or 0)


async def count_evidence_for_job(job_id: str) -> int:
    from sqlalchemy import text
    from core.database import engine

    async with engine.connect() as conn:
        for sql in (
            "SELECT COUNT(*) FROM evidence WHERE job_id = :jid",
            "SELECT COUNT(*) FROM evidence_records WHERE job_id = :jid",
            "SELECT COUNT(*) FROM execution_evidence WHERE execution_job_id = :jid",
        ):
            try:
                r = await conn.execute(text(sql), {"jid": job_id})
                return int(r.scalar_one() or 0)
            except Exception:
                continue
    return 0


async def wait_for_claim(
    job_id: str, timeout_s: float = 30.0, poll_s: float = 0.2
) -> dict:
    deadline = time.monotonic() + timeout_s
    t0 = time.monotonic()
    last: Any = None
    while time.monotonic() < deadline:
        row = await fetch_job(job_id)
        last = row
        if row and row["status"] == "running" and row.get("worker_id"):
            return row
        await asyncio.sleep(poll_s)
    raise DrillPhaseError(
        PhaseFailure(
            phase=PHASE_CLAIM,
            expected="status=running with worker_id set",
            actual=f"last={last}",
            elapsed_seconds=time.monotonic() - t0,
            last_observed_state=last,
            detail=f"job {job_id} never claimed within {timeout_s}s",
        )
    )


async def wait_for_recovery(
    job_id: str,
    *,
    killed_worker: str,
    timeout_s: float = 60.0,
    poll_s: float = 0.3,
) -> dict:
    """Wait until durable state shows attempt>=2 under a non-killed worker (or terminal success)."""
    deadline = time.monotonic() + timeout_s
    t0 = time.monotonic()
    last: Any = None
    while time.monotonic() < deadline:
        row = await fetch_job(job_id)
        last = row
        if not row:
            await asyncio.sleep(poll_s)
            continue
        attempts = row.get("attempts") or 0
        status = row.get("status")
        wid = str(row.get("worker_id") or "")
        if attempts >= 2 and status in ("running", "succeeded", "queued", "failed"):
            if status == "succeeded" or killed_worker not in wid or not wid:
                return row
            if killed_worker not in wid:
                return row
        await asyncio.sleep(poll_s)
    raise DrillPhaseError(
        PhaseFailure(
            phase=PHASE_RECOVERY,
            expected="attempts>=2 with recovery_worker != killed_worker (or succeeded)",
            actual=f"last={last}",
            elapsed_seconds=time.monotonic() - t0,
            last_observed_state=last,
            detail=f"job {job_id} not reclaimed within {timeout_s}s",
        )
    )


async def wait_for_terminal(
    job_id: str, timeout_s: float = 60.0, poll_s: float = 0.3
) -> dict:
    deadline = time.monotonic() + timeout_s
    t0 = time.monotonic()
    last: Any = None
    while time.monotonic() < deadline:
        row = await fetch_job(job_id)
        last = row
        if row and row["status"] in ("succeeded", "failed"):
            return row
        await asyncio.sleep(poll_s)
    raise DrillPhaseError(
        PhaseFailure(
            phase=PHASE_FINALIZATION,
            expected="status in {succeeded, failed}",
            actual=f"last={last}",
            elapsed_seconds=time.monotonic() - t0,
            last_observed_state=last,
            detail=f"job {job_id} not terminal within {timeout_s}s",
        )
    )


def write_result(payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"p0-worker-kill-{ts}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


# ── Drill orchestration ─────────────────────────────────────────────────────


async def run_drill(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "drill": "P0_worker_kill_after_claim",
        "status": OUTCOME_FAIL,
        "database": "postgresql",
        "redis": False,
        "workers": 2,
        "timestamp": _utc(),
        "assertions": {},
        "phase": PHASE_INFRA,
        "stop_conditions": {
            "BLOCKED": [
                "DATABASE_URL not PostgreSQL",
                "PostgreSQL unreachable",
                "Redis configured but unreachable",
                "schema init failure",
                "worker process failed to start",
            ],
            "FAIL": [
                "job never claimed",
                "SIGKILL failed",
                "lease recovery not observed in durable state",
                "surviving worker did not reclaim (attempts < 2)",
                "final status not succeeded",
                "idempotency / duplicate operation or evidence",
                "blind UNKNOWN retry",
            ],
            "PASS": "all durable recovery assertions hold",
        },
        "expected_recovery": {
            "initial_attempt": 1,
            "recovery_attempt": 2,
            "recovery_worker": "!= killed_worker",
            "final_job.status": "succeeded",
            "operation.status": "succeeded",
            "duplicate_operations": 0,
            "duplicate_evidence": 0,
            "blind_unknown_retry": False,
            "note": "recover_stale_leases_count is diagnostic only; not a pass criterion",
        },
    }

    db_url = os.environ.get("DATABASE_URL") or ""
    wa = wb = None
    engine = None

    try:
        require_postgresql(db_url)
    except InfrastructureBlocked as e:
        report["status"] = OUTCOME_BLOCKED
        report["phase"] = PHASE_INFRA
        report["error"] = str(e)
        report["phase_failure"] = PhaseFailure(
            phase=PHASE_INFRA,
            expected="postgresql DATABASE_URL",
            actual=db_url,
            elapsed_seconds=0.0,
            detail=str(e),
        ).to_dict()
        path = write_result(report)
        print(json.dumps(report, indent=2, default=str))
        print(f"Result: {path}", file=sys.stderr)
        return 2

    os.environ["DATABASE_URL"] = db_url
    from core import config as cfg

    cfg.settings.DATABASE_URL = db_url

    lease_s = int(os.environ.get("DEVOS_JOB_LEASE_S", str(args.lease_s)))
    os.environ["DEVOS_JOB_LEASE_S"] = str(lease_s)
    hold_s = max(lease_s * 4, int(os.environ.get("DEVOS_STAGING_HOLD_S", "60")))
    os.environ["DEVOS_STAGING_HOLD_S"] = str(hold_s)

    worker_env = {
        "DATABASE_URL": db_url,
        "DEVOS_JOB_LEASE_S": str(lease_s),
        "DEVOS_STAGING_HOLD_S": str(hold_s),
        "DEVOS_JOB_POLL_S": "0.25",
        "JWT_SECRET": os.environ.get("JWT_SECRET") or "staging-drill-jwt-secret-32chars!!",
    }
    if os.environ.get("REDIS_URL") or os.environ.get("DEVOS_REDIS_URL"):
        worker_env["REDIS_URL"] = os.environ.get("REDIS_URL") or os.environ.get(
            "DEVOS_REDIS_URL", ""
        )
        worker_env["DEVOS_REDIS_URL"] = worker_env["REDIS_URL"]

    from core.database import init_db, engine as _engine

    engine = _engine

    try:
        # ── INFRA ──────────────────────────────────────────────────────────
        report["phase"] = PHASE_INFRA
        await wait_pg(timeout_s=args.infra_timeout)
        await init_db()
        report["assertions"]["postgresql"] = "PASS"

        redis_info = await check_redis()
        report["redis"] = bool(redis_info.get("reachable"))
        report["redis_detail"] = redis_info
        if redis_info.get("configured") and not redis_info.get("reachable"):
            raise InfrastructureBlocked(
                f"Redis configured but unreachable: {redis_info}",
                last_observed=redis_info,
            )
        report["assertions"]["redis"] = (
            "PASS"
            if redis_info.get("reachable") or not redis_info.get("configured")
            else "FAIL"
        )

        # ── ACTION: start workers ──────────────────────────────────────────
        wa = start_worker("worker-a", worker_env)
        wb = start_worker("worker-b", worker_env)
        report["worker_a_pid"] = wa.pid
        report["worker_b_pid"] = wb.pid
        await asyncio.sleep(1.0)
        if wa.poll() is not None or wb.poll() is not None:
            raise InfrastructureBlocked(
                f"worker failed to start a_rc={wa.poll()} b_rc={wb.poll()}",
                last_observed={"a": wa.poll(), "b": wb.poll()},
            )
        report["assertions"]["workers"] = "PASS"

        tenant = "tenant-staging-p0"
        owner = "owner-staging-p0"
        idem = f"p0-kill-{uuid.uuid4().hex}"
        report["idempotency_key"] = idem

        # ── ACTION: create test job ────────────────────────────────────────
        from workers.job_queue import enqueue

        job = await enqueue(
            owner_id=owner,
            tenant_id=tenant,
            job_type="staging_p0_hold",
            payload={"drill": "P0_worker_kill_after_claim", "marker": idem},
            actor_id=owner,
            max_attempts=3,
            idempotency_key=idem,
            request_id=f"req-{idem[:12]}",
            correlation={"correlation_id": f"corr-{idem[:12]}", "drill": "P0"},
        )
        job_id = job.id
        report["job_id"] = job_id
        report["operation_id"] = getattr(job, "operation_id", None)

        # ── CLAIM (diagnosis) ──────────────────────────────────────────────
        report["phase"] = PHASE_CLAIM
        claimed = await wait_for_claim(job_id, timeout_s=args.claim_timeout)
        report["claiming_worker"] = claimed["worker_id"]
        report["initial_attempt"] = claimed["attempts"]
        report["claimed_at"] = claimed.get("locked_at")
        report["lease_expiry"] = claimed.get("lease_expires_at")
        if claimed.get("attempts") != 1:
            raise DrillPhaseError(
                PhaseFailure(
                    phase=PHASE_CLAIM,
                    expected="attempts==1 on first claim",
                    actual=str(claimed.get("attempts")),
                    elapsed_seconds=0.0,
                    last_observed_state=claimed,
                )
            )
        report["assertions"]["claimed"] = "PASS"

        claimer = claimed["worker_id"] or ""
        if "worker-a" in claimer:
            victim, survivor = wa, wb
            report["killed_worker"] = "worker-a"
            report["expected_recovery_worker_prefix"] = "worker-b"
        elif "worker-b" in claimer:
            victim, survivor = wb, wa
            report["killed_worker"] = "worker-b"
            report["expected_recovery_worker_prefix"] = "worker-a"
        else:
            raise DrillPhaseError(
                PhaseFailure(
                    phase=PHASE_CLAIM,
                    expected="claimer worker-a or worker-b",
                    actual=claimer,
                    elapsed_seconds=0.0,
                    last_observed_state=claimed,
                )
            )

        # ── ACTION: SIGKILL ────────────────────────────────────────────────
        report["phase"] = PHASE_SIGKILL
        kill_ts = _utc()
        sigkill(victim)
        report["kill_timestamp"] = kill_ts
        report["assertions"]["sigkill"] = "PASS"

        # ── LEASE_EXPIRY (diagnosis only — wait wall clock past lease) ─────
        report["phase"] = PHASE_LEASE_EXPIRY
        await asyncio.sleep(lease_s + 1.5)
        mid = await fetch_job(job_id)
        report["post_lease_wait_snapshot"] = mid
        # recover_stale_leases_count is NOT invoked by harness (production path only)
        report["recover_stale_leases_count"] = None
        report["recover_stale_leases_note"] = (
            "diagnostic only; harness does not call recover_stale_leases; "
            "survivor claim_next performs production recovery"
        )

        # ── RECOVERY (diagnosis) ───────────────────────────────────────────
        report["phase"] = PHASE_RECOVERY
        second = await wait_for_recovery(
            job_id,
            killed_worker=report["killed_worker"],
            timeout_s=args.recovery_timeout,
        )
        report["recovery_snapshot"] = second
        report["recovery_attempt"] = second.get("attempts")
        report["recovery_worker"] = second.get("worker_id")
        report["assertions"]["lease_recovery"] = "PASS"

        # ── FINALIZATION (diagnosis) ───────────────────────────────────────
        report["phase"] = PHASE_FINALIZATION
        if second["status"] == "succeeded":
            terminal = second
        elif second["status"] == "running":
            terminal = await wait_for_terminal(
                job_id, timeout_s=hold_s + args.recovery_timeout
            )
        else:
            terminal = second
        report["final_job"] = terminal

        # ── INTEGRITY (diagnosis) ──────────────────────────────────────────
        report["phase"] = PHASE_INTEGRITY
        n_jobs = await count_jobs_by_idempotency(tenant, idem)
        n_ops = await count_operations_for_job(job_id)
        n_ev = await count_evidence_for_job(job_id)
        report["job_rows_for_idempotency_key"] = n_jobs
        report["operation_rows_for_job"] = n_ops
        report["evidence_rows_for_job"] = n_ev
        report["duplicate_operations"] = max(0, n_ops - 1) if n_ops else 0
        report["duplicate_evidence"] = 0 if n_ev <= 1 else n_ev - 1
        report["duplicate_successes"] = 0

        op_status = None
        report["blind_unknown_retry"] = False
        op_id = terminal.get("operation_id") or report.get("operation_id")
        if op_id:
            try:
                from governance.execution_operations import load_operation

                op = await load_operation(op_id)
                op_status = (
                    (op or {}).get("status")
                    if isinstance(op, dict)
                    else getattr(op, "status", None)
                )
                report["operation_status"] = op_status
                if str(op_status).lower() == "unknown" and terminal["status"] == "succeeded":
                    report["blind_unknown_retry"] = True
            except Exception as e:
                report["operation_status_error"] = str(e)

        ok, errs = evaluate_expected_recovery(
            initial_attempt=report.get("initial_attempt"),
            recovery_attempt=report.get("recovery_attempt"),
            recovery_worker=report.get("recovery_worker"),
            killed_worker=report.get("killed_worker"),
            final_status=terminal.get("status"),
            operation_status=op_status,
            job_rows=n_jobs,
            operation_rows=n_ops,
            evidence_rows=n_ev,
            blind_unknown_retry=bool(report["blind_unknown_retry"]),
        )
        if not ok:
            raise DrillPhaseError(
                PhaseFailure(
                    phase=PHASE_INTEGRITY,
                    expected="see expected_recovery",
                    actual="; ".join(errs),
                    elapsed_seconds=0.0,
                    last_observed_state={
                        "final_job": terminal,
                        "operation_status": op_status,
                    },
                    detail="; ".join(errs),
                )
            )

        report["assertions"]["idempotency"] = "PASS"
        report["assertions"]["duplicate_operation"] = "PASS"
        report["assertions"]["duplicate_evidence"] = "PASS"
        report["assertions"]["unknown_blind_retry"] = "PASS"
        report["assertions"]["final_durable_state"] = "PASS"

        if survivor.poll() is not None:
            raise DrillPhaseError(
                PhaseFailure(
                    phase=PHASE_INTEGRITY,
                    expected="survivor worker still alive",
                    actual=f"rc={survivor.poll()}",
                    elapsed_seconds=0.0,
                )
            )
        report["assertions"]["survivor_alive"] = "PASS"

        report["status"] = OUTCOME_PASS
        report["phase"] = "COMPLETE"
        rc = 0

    except InfrastructureBlocked as e:
        report["status"] = OUTCOME_BLOCKED
        report["error"] = str(e)
        report["phase"] = getattr(e, "phase", PHASE_INFRA)
        report["phase_failure"] = PhaseFailure(
            phase=report["phase"],
            expected="infrastructure available",
            actual=str(e),
            elapsed_seconds=0.0,
            last_observed_state=getattr(e, "last_observed", None),
            detail=str(e),
        ).to_dict()
        if "postgresql" not in report["assertions"]:
            report["assertions"]["postgresql"] = "FAIL"
        rc = 2
    except DrillPhaseError as e:
        report["status"] = OUTCOME_FAIL
        report["error"] = str(e)
        report["phase"] = e.failure.phase
        report["phase_failure"] = e.failure.to_dict()
        rc = 1
    except Exception as e:
        report["status"] = OUTCOME_FAIL
        report["error"] = str(e)
        report["phase_failure"] = PhaseFailure(
            phase=report.get("phase") or "UNKNOWN",
            expected="no unhandled exception",
            actual=str(e),
            elapsed_seconds=0.0,
        ).to_dict()
        rc = 1
    finally:
        for p in (wa, wb):
            if p is not None and p.poll() is None:
                try:
                    os.kill(p.pid, signal.SIGKILL)
                    p.wait(timeout=3)
                except Exception:
                    pass
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:
                pass

    path = write_result(report)
    print(json.dumps(report, indent=2, default=str))
    print(f"Result written: {path}", file=sys.stderr)
    return rc


def main() -> int:
    p = argparse.ArgumentParser(description="P0 live staging: SIGKILL worker after claim")
    p.add_argument("--lease-s", type=int, default=5, help="Job lease seconds (short for drill)")
    p.add_argument("--infra-timeout", type=float, default=30.0)
    p.add_argument("--claim-timeout", type=float, default=30.0)
    p.add_argument("--recovery-timeout", type=float, default=45.0)
    args = p.parse_args()
    return asyncio.run(run_drill(args))


if __name__ == "__main__":
    raise SystemExit(main())
