#!/usr/bin/env python3
"""LIVE STAGING DRILL — P1 external provider drop-response → UNKNOWN → reconcile.

Exit: 0 PASS | 1 FAIL | 2 BLOCKED
Does not import app.py. Does not SET status=unknown. Does not call
reconcile_operation as a harness shortcut (workers run staging_p1_reconcile).
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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.request import urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "data" / "staging-results"

OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_BLOCKED = "PASS", "FAIL", "BLOCKED"


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
    def __init__(self, msg: str, *, last_observed: Any = None):
        super().__init__(msg)
        self.last_observed = last_observed


class DrillPhaseError(Exception):
    def __init__(self, failure: PhaseFailure):
        super().__init__(str(failure.to_dict()))
        self.failure = failure


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_postgresql_url(url: str) -> bool:
    u = (url or "").lower()
    return u.startswith("postgresql") or u.startswith("postgres")


def classify_outcome(*, blocked: bool = False, failed: bool = False) -> str:
    if blocked:
        return OUTCOME_BLOCKED
    if failed:
        return OUTCOME_FAIL
    return OUTCOME_PASS


def evaluate_p1_expected(
    *,
    provider_accepted: bool,
    provider_side_effect_count: int,
    provider_execution_count_before: int,
    provider_execution_count_final: int,
    unknown_observed: bool,
    blind_retry: bool,
    final_operation_status: str,
    final_job_status: str,
    job_rows: int,
    operation_rows: int,
) -> tuple[bool, list[str]]:
    errs = []
    if not provider_accepted:
        errs.append("provider_accepted expected True")
    if provider_side_effect_count != 1:
        errs.append(f"provider_side_effect_count expected 1 got {provider_side_effect_count}")
    if provider_execution_count_before != 1:
        errs.append(f"execution_count before reconcile expected 1 got {provider_execution_count_before}")
    if provider_execution_count_final != 1:
        errs.append(f"execution_count final expected 1 got {provider_execution_count_final}")
    if not unknown_observed:
        errs.append("UNKNOWN was not observed before reconciliation")
    if blind_retry:
        errs.append("blind UNKNOWN retry detected")
    if str(final_operation_status).lower() != "succeeded":
        errs.append(f"final operation expected succeeded got {final_operation_status}")
    if str(final_job_status).lower() != "succeeded":
        errs.append(f"final job expected succeeded got {final_job_status}")
    if job_rows != 1:
        errs.append(f"job_rows expected 1 got {job_rows}")
    if operation_rows != 1:
        errs.append(f"operation_rows expected 1 got {operation_rows}")
    return (len(errs) == 0, errs)


def provider_get(url: str, timeout: float = 3.0) -> dict:
    with urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def start_proc(cmd: list, env: dict) -> subprocess.Popen:
    e = {**os.environ, **env, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(cmd, cwd=str(ROOT), env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def sigkill(proc: subprocess.Popen) -> None:
    if proc and proc.poll() is None:
        try:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
        except Exception:
            pass


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
            await asyncio.sleep(0.4)
    raise InfrastructureBlocked(f"PostgreSQL unreachable: {last}")


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


def wait_provider(base: str, timeout_s: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout_s
    t0 = time.monotonic()
    last = None
    while time.monotonic() < deadline:
        try:
            h = provider_get(f"{base}/health")
            if h.get("ok"):
                return h
            last = h
        except Exception as e:
            last = str(e)
        time.sleep(0.3)
    raise InfrastructureBlocked(
        f"provider unavailable within {timeout_s}s: {last}",
        last_observed={"elapsed": time.monotonic() - t0, "last": last},
    )


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
            "result": job.result,
            "error": job.error,
        }


async def fetch_op(op_id: str) -> Optional[dict]:
    from governance.execution_operations import load_operation
    return await load_operation(op_id)


async def wait_op_status(op_id: str, statuses: set[str], timeout_s: float, phase: str) -> dict:
    deadline = time.monotonic() + timeout_s
    t0 = time.monotonic()
    last = None
    while time.monotonic() < deadline:
        op = await fetch_op(op_id)
        last = op
        if op and str(op.get("status") or "").lower() in statuses:
            return op
        await asyncio.sleep(0.25)
    raise DrillPhaseError(
        PhaseFailure(
            phase=phase,
            expected=f"status in {statuses}",
            actual=str(last),
            elapsed_seconds=time.monotonic() - t0,
            last_observed_state=last,
        )
    )


async def wait_job_status(job_id: str, statuses: set[str], timeout_s: float, phase: str) -> dict:
    deadline = time.monotonic() + timeout_s
    t0 = time.monotonic()
    last = None
    while time.monotonic() < deadline:
        row = await fetch_job(job_id)
        last = row
        if row and row.get("status") in statuses:
            return row
        await asyncio.sleep(0.25)
    raise DrillPhaseError(
        PhaseFailure(
            phase=phase,
            expected=f"job status in {statuses}",
            actual=str(last),
            elapsed_seconds=time.monotonic() - t0,
            last_observed_state=last,
        )
    )


async def count_jobs(tenant: str, key: str) -> int:
    from sqlalchemy import select, func
    from core.database import AsyncSessionLocal, ExecutionJob

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(func.count()).select_from(ExecutionJob).where(
                ExecutionJob.tenant_id == tenant,
                ExecutionJob.idempotency_key == key,
            )
        )
        return int(r.scalar_one() or 0)


async def count_ops(job_id: str) -> int:
    from sqlalchemy import select, func
    from core.database import AsyncSessionLocal, ExecutionOperation

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(func.count()).select_from(ExecutionOperation).where(
                ExecutionOperation.execution_job_id == job_id
            )
        )
        return int(r.scalar_one() or 0)


def write_result(payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"p1-unknown-reconciliation-{ts}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


async def run_drill(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "drill": "P1_external_provider_drop_response",
        "status": OUTCOME_FAIL,
        "database": "postgresql",
        "redis": False,
        "workers": 2,
        "provider": False,
        "timestamp": _utc(),
        "phase": "BOOTSTRAP",
        "assertions": {},
        "stop_conditions": {
            "BLOCKED": [
                "DATABASE_URL not PostgreSQL",
                "PostgreSQL unreachable",
                "Redis configured but unreachable",
                "provider unavailable",
                "worker failed to start",
            ],
            "FAIL": [
                "provider did not accept",
                "UNKNOWN not observed",
                "blind retry (execution_count>1 before reconcile)",
                "reconciliation did not succeed",
                "final op/job not succeeded",
                "duplicate side effect or operation",
            ],
            "PASS": "all expected_state assertions hold",
        },
        "expected_state": {
            "provider_accepted": True,
            "provider_side_effect_count": 1,
            "operation_status_before_reconciliation": "unknown",
            "blind_unknown_retry": False,
            "provider_execution_count_before_reconciliation": 1,
            "reconciliation_result": "succeeded",
            "final_operation_status": "succeeded",
            "final_job_status": "succeeded",
            "provider_execution_count_final": 1,
            "provider_side_effect_count_final": 1,
            "job_rows": 1,
            "operation_rows": 1,
        },
    }
    procs: list[subprocess.Popen] = []
    engine = None
    provider_base = (os.environ.get("P1_PROVIDER_URL") or f"http://127.0.0.1:{os.environ.get('P1_PROVIDER_PORT', '8099')}").rstrip("/")

    try:
        db_url = os.environ.get("DATABASE_URL") or ""
        if not is_postgresql_url(db_url):
            raise InfrastructureBlocked(f"DATABASE_URL must be PostgreSQL got {db_url!r}")
        os.environ["DATABASE_URL"] = db_url
        from core import config as cfg
        cfg.settings.DATABASE_URL = db_url

        from core.database import init_db, engine as _engine
        engine = _engine
        await wait_pg(timeout_s=args.infra_timeout)
        await init_db()
        report["assertions"]["postgresql"] = "PASS"

        redis_info = await check_redis()
        report["redis"] = bool(redis_info.get("reachable"))
        report["redis_detail"] = redis_info
        if redis_info.get("configured") and not redis_info.get("reachable"):
            raise InfrastructureBlocked(f"Redis unreachable: {redis_info}")
        report["assertions"]["redis"] = "PASS" if redis_info.get("reachable") or not redis_info.get("configured") else "FAIL"

        # ACTION: start provider
        report["phase"] = "PROVIDER_READY"
        prov_env = {
            "P1_PROVIDER_PORT": os.environ.get("P1_PROVIDER_PORT", "8099"),
            "P1_DROP_RESPONSE": "true",
        }
        prov = start_proc([sys.executable, str(ROOT / "scripts" / "staging_provider_stub.py")], prov_env)
        procs.append(prov)
        wait_provider(provider_base, timeout_s=15)
        report["provider"] = True
        report["assertions"]["provider"] = "PASS"

        # ACTION: workers
        report["phase"] = "WORKERS_READY"
        lease_s = int(os.environ.get("DEVOS_JOB_LEASE_S", "5"))
        wenv = {
            "DATABASE_URL": db_url,
            "DEVOS_JOB_LEASE_S": str(lease_s),
            "DEVOS_JOB_POLL_S": "0.25",
            "P1_PROVIDER_URL": provider_base,
            "JWT_SECRET": os.environ.get("JWT_SECRET") or "staging-drill-jwt-secret-32chars!!",
        }
        if os.environ.get("REDIS_URL") or os.environ.get("DEVOS_REDIS_URL"):
            wenv["REDIS_URL"] = os.environ.get("REDIS_URL") or os.environ["DEVOS_REDIS_URL"]
            wenv["DEVOS_REDIS_URL"] = wenv["REDIS_URL"]
        for wid in ("worker-a", "worker-b"):
            wenv2 = {**wenv, "DEVOS_WORKER_ID": wid}
            p = start_proc([sys.executable, str(ROOT / "scripts" / "run_job_worker.py")], wenv2)
            procs.append(p)
        await asyncio.sleep(1.0)
        if any(p.poll() is not None for p in procs[1:]):
            raise InfrastructureBlocked("worker failed to start")
        report["assertions"]["workers"] = "PASS"

        # ACTION: enqueue external job
        report["phase"] = "JOB_CREATED"
        from workers.job_queue import enqueue

        tenant = "tenant-staging-p1"
        owner = "owner-staging-p1"
        idem = f"p1-unknown-{uuid.uuid4().hex}"
        provider_key = f"p1-prov-{uuid.uuid4().hex}"
        report["idempotency_key"] = idem
        report["provider_operation_key"] = provider_key

        job = await enqueue(
            owner_id=owner,
            tenant_id=tenant,
            job_type="staging_p1_external",
            payload={
                "provider_operation_key": provider_key,
                "drop_response": True,
                "drill": "P1",
            },
            actor_id=owner,
            max_attempts=1,
            idempotency_key=idem,
            correlation={"correlation_id": f"corr-{idem[:12]}"},
        )
        # inject operation_id into payload for handler (enqueue may already set)
        job_id = job.id
        op_id = getattr(job, "operation_id", None)
        report["job_id"] = job_id
        report["operation_id"] = op_id
        if not op_id:
            raise DrillPhaseError(PhaseFailure("JOB_CREATED", "operation_id set", "None", 0.0))

        # PROVIDER_ACCEPTED + UNKNOWN
        report["phase"] = "UNKNOWN_OBSERVED"
        # Wait until op is unknown (production path)
        op_unknown = await wait_op_status(op_id, {"unknown"}, timeout_s=args.unknown_timeout, phase="UNKNOWN_OBSERVED")
        report["operation_status_before_reconciliation"] = op_unknown.get("status")
        report["assertions"]["unknown_observed"] = "PASS"

        # Provider independent proof
        report["phase"] = "PROVIDER_ACCEPTED"
        prov_state = provider_get(f"{provider_base}/status/{provider_key}")
        report["provider_before_reconcile"] = prov_state
        if not prov_state.get("accepted"):
            raise DrillPhaseError(PhaseFailure(
                "PROVIDER_ACCEPTED", "accepted=true", str(prov_state), 0.0, prov_state
            ))
        if int(prov_state.get("side_effect_count") or 0) != 1:
            raise DrillPhaseError(PhaseFailure(
                "PROVIDER_ACCEPTED", "side_effect_count=1", str(prov_state), 0.0, prov_state
            ))
        report["assertions"]["provider_accepted"] = "PASS"
        exec_before = int(prov_state.get("execution_count") or 0)

        # NO_RETRY_WINDOW
        report["phase"] = "NO_RETRY_WINDOW"
        await asyncio.sleep(args.no_retry_window_s)
        prov_mid = provider_get(f"{provider_base}/status/{provider_key}")
        report["provider_no_retry_window"] = prov_mid
        exec_mid = int(prov_mid.get("execution_count") or 0)
        if exec_mid > 1 or int(prov_mid.get("side_effect_count") or 0) > 1:
            raise DrillPhaseError(PhaseFailure(
                "NO_RETRY_WINDOW",
                "execution_count==1 and side_effect_count==1",
                str(prov_mid),
                args.no_retry_window_s,
                prov_mid,
                detail="blind retry of UNKNOWN",
            ))
        report["blind_unknown_retry"] = False
        report["assertions"]["no_blind_retry"] = "PASS"
        report["provider_execution_count_before_reconciliation"] = exec_before

        # ACTION: enqueue reconcile job (workers execute production reconcile path)
        report["phase"] = "RECONCILIATION"
        recon_idem = f"p1-recon-{uuid.uuid4().hex}"
        recon_job = await enqueue(
            owner_id=owner,
            tenant_id=tenant,
            job_type="staging_p1_reconcile",
            payload={
                "target_operation_id": op_id,
                "provider_operation_key": provider_key,
            },
            actor_id=owner,
            max_attempts=2,
            idempotency_key=recon_idem,
        )
        report["reconcile_job_id"] = recon_job.id
        await wait_job_status(recon_job.id, {"succeeded"}, timeout_s=args.reconcile_timeout, phase="RECONCILIATION")
        op_final = await wait_op_status(op_id, {"succeeded"}, timeout_s=args.reconcile_timeout, phase="RECONCILIATION")
        report["operation_status_after_reconciliation"] = op_final.get("status")
        report["assertions"]["reconciliation"] = "PASS"

        # FINALIZATION — original external job may be failed permanent; op must be succeeded
        report["phase"] = "FINALIZATION"
        ext_job = await fetch_job(job_id)
        report["external_job_final"] = ext_job
        # Integrity: provider counts still 1
        prov_final = provider_get(f"{provider_base}/status/{provider_key}")
        report["provider_final"] = prov_final

        n_jobs = await count_jobs(tenant, idem)
        n_ops = await count_ops(job_id)
        report["integrity"] = {
            "job_rows_for_idempotency_key": n_jobs,
            "operation_rows_for_job": n_ops,
            "duplicate_operations": max(0, n_ops - 1),
            "provider_execution_count_final": int(prov_final.get("execution_count") or 0),
            "provider_side_effect_count_final": int(prov_final.get("side_effect_count") or 0),
        }

        ok, errs = evaluate_p1_expected(
            provider_accepted=bool(prov_final.get("accepted")),
            provider_side_effect_count=int(prov_final.get("side_effect_count") or 0),
            provider_execution_count_before=exec_before,
            provider_execution_count_final=int(prov_final.get("execution_count") or 0),
            unknown_observed=str(report.get("operation_status_before_reconciliation")).lower() == "unknown",
            blind_retry=False,
            final_operation_status=str(op_final.get("status") or ""),
            final_job_status="succeeded",  # reconcile job
            job_rows=n_jobs,
            operation_rows=n_ops,
        )
        if not ok:
            raise DrillPhaseError(PhaseFailure(
                "INTEGRITY", "expected_state", "; ".join(errs), 0.0,
                last_observed_state={"errs": errs, "provider": prov_final, "op": op_final},
            ))

        report["assertions"]["final_operation"] = "PASS"
        report["assertions"]["provider_side_effect_once"] = "PASS"
        report["assertions"]["idempotency"] = "PASS"
        report["status"] = OUTCOME_PASS
        report["phase"] = "COMPLETE"
        rc = 0
    except InfrastructureBlocked as e:
        report["status"] = OUTCOME_BLOCKED
        report["error"] = str(e)
        report["phase_failure"] = PhaseFailure(
            report.get("phase") or "BOOTSTRAP", "infra ok", str(e), 0.0, getattr(e, "last_observed", None)
        ).to_dict()
        rc = 2
    except DrillPhaseError as e:
        report["status"] = OUTCOME_FAIL
        report["error"] = str(e)
        report["phase_failure"] = e.failure.to_dict()
        report["phase"] = e.failure.phase
        rc = 1
    except Exception as e:
        report["status"] = OUTCOME_FAIL
        report["error"] = str(e)
        report["phase_failure"] = PhaseFailure(
            report.get("phase") or "UNKNOWN", "no exception", str(e), 0.0
        ).to_dict()
        rc = 1
    finally:
        for p in procs:
            sigkill(p)
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
    p = argparse.ArgumentParser(description="P1 live staging: drop-response UNKNOWN reconcile")
    p.add_argument("--infra-timeout", type=float, default=30.0)
    p.add_argument("--unknown-timeout", type=float, default=40.0)
    p.add_argument("--no-retry-window-s", type=float, default=4.0)
    p.add_argument("--reconcile-timeout", type=float, default=40.0)
    args = p.parse_args()
    return asyncio.run(run_drill(args))


if __name__ == "__main__":
    raise SystemExit(main())
