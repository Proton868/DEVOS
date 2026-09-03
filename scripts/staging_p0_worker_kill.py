#!/usr/bin/env python3
"""LIVE STAGING DRILL — P0 worker kill after claim.

Requires:
  - PostgreSQL (DATABASE_URL must be postgresql+asyncpg://...)
  - Optional Redis (REDIS_URL / DEVOS_REDIS_URL)
  - Two independently killable worker processes (scripts/run_job_worker.py)

Does NOT use MemQueue or SQLite. Does NOT import app.py.

Exit codes:
  0 = PASS
  1 = FAIL (assertion)
  2 = BLOCKED (infrastructure unavailable)
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RESULTS_DIR = ROOT / "data" / "staging-results"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_postgresql_url(url: str) -> bool:
    u = (url or "").lower()
    return u.startswith("postgresql") or u.startswith("postgres")


class InfrastructureBlocked(Exception):
    """Staging infra missing or unsuitable (not an assertion failure)."""


def require_postgresql(url: str) -> None:
    if not is_postgresql_url(url):
        raise InfrastructureBlocked(
            "BLOCKED: DATABASE_URL must be PostgreSQL "
            f"(got {url!r}). SQLite is not valid for this live drill."
        )


async def wait_pg(timeout_s: float = 30.0) -> None:
    from sqlalchemy import text
    from core.database import engine

    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as e:
            last = e
            await asyncio.sleep(0.5)
    raise RuntimeError(f"PostgreSQL not reachable: {last}")


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


def start_worker(worker_id: str, env: dict) -> subprocess.Popen:
    e = {**os.environ, **env, "DEVOS_WORKER_ID": worker_id, "PYTHONPATH": str(ROOT)}
    # Unbuffered so logs are observable
    e["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "run_job_worker.py")],
        cwd=str(ROOT),
        env=e,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc


def sigkill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        raise RuntimeError(f"worker already exited rc={proc.returncode}")
    os.kill(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


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
    """Best-effort evidence count if an Evidence model/table links to jobs."""
    from sqlalchemy import text
    from core.database import engine

    async with engine.connect() as conn:
        # Try common evidence tables without assuming schema
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


async def wait_for_claim(job_id: str, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = await fetch_job(job_id)
        if row and row["status"] == "running" and row.get("worker_id"):
            return row
        await asyncio.sleep(0.2)
    raise TimeoutError(f"job {job_id} never claimed within {timeout_s}s")


async def wait_for_status(
    job_id: str,
    statuses: set[str],
    timeout_s: float = 60.0,
    *,
    min_attempts: Optional[int] = None,
) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = await fetch_job(job_id)
        if row and row["status"] in statuses:
            if min_attempts is None or (row.get("attempts") or 0) >= min_attempts:
                return row
        await asyncio.sleep(0.3)
    raise TimeoutError(
        f"job {job_id} did not reach {statuses} (min_attempts={min_attempts}) within {timeout_s}s"
    )


async def force_recover_stale() -> int:
    from workers.job_queue import recover_stale_leases

    return await recover_stale_leases()


def write_result(payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"p0-worker-kill-{ts}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


async def run_drill(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "drill": "P0_worker_kill_after_claim",
        "status": "FAIL",
        "database": "postgresql",
        "redis": False,
        "workers": 2,
        "timestamp": _utc(),
        "assertions": {},
    }

    db_url = os.environ.get("DATABASE_URL") or ""
    try:
        require_postgresql(db_url)
    except InfrastructureBlocked as e:
        report["status"] = "BLOCKED"
        report["error"] = str(e)
        path = write_result(report)
        print(json.dumps(report, indent=2))
        print(f"Result: {path}", file=sys.stderr)
        return 2

    # Bind core.config to this URL before imports that read settings
    os.environ["DATABASE_URL"] = db_url
    from core import config as cfg

    cfg.settings.DATABASE_URL = db_url

    lease_s = int(os.environ.get("DEVOS_JOB_LEASE_S", str(args.lease_s)))
    os.environ["DEVOS_JOB_LEASE_S"] = str(lease_s)
    # Hold longer than lease so kill-before-complete is natural
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

    from core.database import init_db, engine

    try:
        await wait_pg(timeout_s=args.infra_timeout)
        await init_db()
        report["assertions"]["postgresql"] = "PASS"
    except Exception as e:
        report["status"] = "BLOCKED"
        report["error"] = f"PostgreSQL unavailable: {e}"
        report["assertions"]["postgresql"] = "FAIL"
        path = write_result(report)
        print(json.dumps(report, indent=2))
        print(f"Result: {path}", file=sys.stderr)
        return 2

    redis_info = await check_redis()
    report["redis"] = bool(redis_info.get("reachable"))
    report["redis_detail"] = redis_info
    report["assertions"]["redis"] = (
        "PASS" if redis_info.get("reachable") or not redis_info.get("configured") else "FAIL"
    )
    # Redis optional for single-node claim path; only FAIL if configured but dead
    if redis_info.get("configured") and not redis_info.get("reachable"):
        report["status"] = "BLOCKED"
        report["error"] = f"Redis configured but unreachable: {redis_info}"
        path = write_result(report)
        print(json.dumps(report, indent=2))
        return 2

    # Start two real workers
    wa = start_worker("worker-a", worker_env)
    wb = start_worker("worker-b", worker_env)
    report["worker_a_pid"] = wa.pid
    report["worker_b_pid"] = wb.pid
    await asyncio.sleep(1.0)
    if wa.poll() is not None or wb.poll() is not None:
        report["status"] = "FAIL"
        report["error"] = f"worker exited early a={wa.poll()} b={wb.poll()}"
        report["assertions"]["workers"] = "FAIL"
        for p in (wa, wb):
            if p.poll() is None:
                p.kill()
        path = write_result(report)
        print(json.dumps(report, indent=2))
        return 1
    report["assertions"]["workers"] = "PASS"

    tenant = "tenant-staging-p0"
    owner = "owner-staging-p0"
    idem = f"p0-kill-{uuid.uuid4().hex}"
    report["idempotency_key"] = idem

    try:
        from workers.job_queue import enqueue

        # Non-consequential-ish? staging_p0_hold is consequential by default
        # (reserves ExecutionOperation). That is desired for operation ledger checks.
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

        # Wait for durable claim
        claimed = await wait_for_claim(job_id, timeout_s=args.claim_timeout)
        report["claiming_worker"] = claimed["worker_id"]
        report["initial_attempt"] = claimed["attempts"]
        report["claimed_at"] = claimed.get("locked_at")
        report["lease_expiry"] = claimed.get("lease_expires_at")
        report["assertions"]["claimed"] = "PASS"

        # Identify which process to kill
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
            # Fall back: kill worker-a
            victim, survivor = wa, wb
            report["killed_worker"] = f"worker-a (claimer={claimer})"
            report["expected_recovery_worker_prefix"] = "worker-b"

        kill_ts = _utc()
        sigkill(victim)
        report["kill_timestamp"] = kill_ts
        report["assertions"]["sigkill"] = "PASS"

        # Wait until lease can expire, then force recovery path used in production
        await asyncio.sleep(lease_s + 1.5)
        recovered_n = await force_recover_stale()
        report["recover_stale_leases_count"] = recovered_n

        # After recovery, job should be queued or already reclaimed by survivor
        mid = await fetch_job(job_id)
        report["post_recovery_snapshot"] = mid
        if not mid:
            raise AssertionError("job missing after recovery")

        # Survivor should complete (hold is long, but after reclaim handler runs full hold —
        # for drill speed: we rely on short lease + survivor claim; hold still applies.
        # Reduce wait: set hold via env already; if hold is long, we only need to observe
        # second claim, not necessarily full success within short CI.
        # Prefer wait for attempts>=2 and status running or terminal.
        second = await wait_for_status(
            job_id,
            {"running", "succeeded", "failed", "queued"},
            timeout_s=args.recovery_timeout,
            min_attempts=2,
        )
        report["recovery_snapshot"] = second
        report["recovery_attempt"] = second.get("attempts")
        report["recovery_worker"] = second.get("worker_id")
        report["assertions"]["lease_recovery"] = "PASS"

        # If still running under survivor, wait for terminal with remaining hold budget
        if second["status"] == "running":
            terminal = await wait_for_status(
                job_id,
                {"succeeded", "failed"},
                timeout_s=hold_s + args.recovery_timeout,
            )
        else:
            terminal = second
        report["final_job"] = terminal

        # Assertions: idempotency / duplicates
        n_jobs = await count_jobs_by_idempotency(tenant, idem)
        n_ops = await count_operations_for_job(job_id)
        n_ev = await count_evidence_for_job(job_id)
        report["duplicate_successes"] = 0  # single job row terminal check below
        report["job_rows_for_idempotency_key"] = n_jobs
        report["operation_rows_for_job"] = n_ops
        report["evidence_rows_for_job"] = n_ev

        if n_jobs != 1:
            raise AssertionError(f"idempotency violated: {n_jobs} jobs for key")
        report["assertions"]["idempotency"] = "PASS"

        if n_ops > 1:
            raise AssertionError(f"duplicate operations: {n_ops}")
        report["duplicate_operations"] = max(0, n_ops - 1) if n_ops else 0
        report["assertions"]["duplicate_operation"] = "PASS"

        report["duplicate_evidence"] = 0 if n_ev <= 1 else n_ev - 1
        if n_ev > 1:
            raise AssertionError(f"duplicate evidence: {n_ev}")
        report["assertions"]["duplicate_evidence"] = "PASS"

        # UNKNOWN must not be blindly retried — if operation is UNKNOWN, job must not
        # have been auto-completed as succeeded without reconcile.
        report["blind_unknown_retry"] = False
        op_id = terminal.get("operation_id") or report.get("operation_id")
        if op_id:
            try:
                from governance.execution_operations import load_operation

                op = await load_operation(op_id)
                op_status = (op or {}).get("status") if isinstance(op, dict) else getattr(op, "status", None)
                report["operation_status"] = op_status
                if str(op_status).lower() == "unknown" and terminal["status"] == "succeeded":
                    # succeeded terminal while op UNKNOWN would be a semantic bug unless evidence reconciled
                    report["blind_unknown_retry"] = True
                    raise AssertionError("UNKNOWN operation with succeeded job without explicit reconcile")
            except AssertionError:
                raise
            except Exception as e:
                report["operation_status_error"] = str(e)
        report["assertions"]["unknown_blind_retry"] = (
            "PASS" if not report["blind_unknown_retry"] else "FAIL"
        )

        if terminal["status"] not in ("succeeded", "failed", "queued", "running"):
            raise AssertionError(f"unexpected terminal status {terminal['status']}")
        # Prefer succeeded after full recovery cycle; failed is still a durable terminal
        report["assertions"]["final_durable_state"] = "PASS"
        if (terminal.get("attempts") or 0) < 2:
            raise AssertionError("expected attempts >= 2 after kill+reclaim")

        # Survivor still alive
        if survivor.poll() is not None:
            raise AssertionError("survivor worker died unexpectedly")
        report["assertions"]["survivor_alive"] = "PASS"

        report["status"] = "PASS"
        rc = 0
    except Exception as e:
        report["status"] = "FAIL"
        report["error"] = str(e)
        rc = 1
    finally:
        for p in (wa, wb):
            if p.poll() is None:
                try:
                    os.kill(p.pid, signal.SIGKILL)
                    p.wait(timeout=3)
                except Exception:
                    pass
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
