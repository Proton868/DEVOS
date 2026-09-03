#!/usr/bin/env python3
"""Standalone ExecutionJob worker process for multi-worker staging.

Usage:
  DEVOS_WORKER_ID=worker-a DEVOS_JOB_LEASE_S=5 \\
    DATABASE_URL=postgresql+asyncpg://... python scripts/run_job_worker.py

Does not start the HTTP API (app.py is never imported).
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [job-worker] %(message)s",
)
logger = logging.getLogger("devos.staging.worker")


async def _handler_staging_p0_hold(job):
    hold = float(os.environ.get("DEVOS_STAGING_HOLD_S", "120"))
    logger.info(
        "staging_p0_hold claimed job=%s worker=%s hold_s=%s attempts=%s",
        job.id,
        os.environ.get("DEVOS_WORKER_ID"),
        hold,
        job.attempts,
    )
    await asyncio.sleep(hold)
    return {
        "status": "succeeded",
        "staging": True,
        "worker_id": os.environ.get("DEVOS_WORKER_ID"),
        "job_id": job.id,
    }


async def _handler_ping(job):
    return {"status": "succeeded", "pong": True, "job_id": job.id}


async def _handler_staging_p1_external(job):
    """Consequential external call: provider accepts, may drop response → UNKNOWN.

    Production path:
      mark_running → HTTP POST /execute → transport failure → mark_unknown
      permanent (no requeue / no blind retry)
    """
    import json
    from urllib.error import URLError, HTTPError
    from urllib.request import Request, urlopen

    from governance.execution_operations import mark_running, mark_unknown, load_operation

    payload = job.payload if isinstance(job.payload, dict) else {}
    op_id = getattr(job, "operation_id", None) or payload.get("operation_id")
    provider_key = payload.get("provider_operation_key") or payload.get("operation_key")
    base = (os.environ.get("P1_PROVIDER_URL") or "http://127.0.0.1:8099").rstrip("/")
    drop = payload.get("drop_response", True)

    if not op_id or not provider_key:
        return {"status": "failed", "error": "missing operation_id or provider_operation_key", "permanent": True}

    await mark_running(op_id)

    body = json.dumps({
        "operation_key": provider_key,
        "payload": {"staging": True, "job_id": job.id},
        "drop_response": bool(drop),
    }).encode()
    req = Request(
        f"{base}/execute",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=float(os.environ.get("P1_PROVIDER_TIMEOUT_S", "5"))) as resp:
            raw = resp.read().decode()
            result = json.loads(raw or "{}")
        # Unexpected success path (drop disabled)
        from governance.execution_operations import complete_operation

        await complete_operation(op_id, success=True)
        return {"status": "succeeded", "provider": result, "job_id": job.id}
    except Exception as e:
        # Transport / dropped response → UNKNOWN (side effect may have occurred)
        await mark_unknown(op_id, f"provider_transport:{type(e).__name__}:{e}")
        logger.warning(
            "staging_p1_external UNKNOWN job=%s op=%s key=%s err=%s",
            job.id, op_id, provider_key, e,
        )
        return {
            "status": "failed",
            "error": f"operation_unknown | {e}",
            "permanent": True,
            "outcome": "unknown",
            "job_id": job.id,
            "operation_id": op_id,
        }


async def _handler_staging_p1_reconcile(job):
    """Production-shaped reconcile: query provider truth → evidence → reconcile_operation.

    Does not re-call /execute. Does not blind-retry UNKNOWN.
    """
    import json
    from urllib.request import Request, urlopen

    from core.database import AsyncSessionLocal, EvidenceRecord, gen_id
    from governance.execution_operations import (
        load_operation, reconcile_operation, complete_operation, OP_UNKNOWN, OP_SUCCEEDED,
    )

    payload = job.payload if isinstance(job.payload, dict) else {}
    op_id = payload.get("target_operation_id") or payload.get("operation_id")
    provider_key = payload.get("provider_operation_key")
    base = (os.environ.get("P1_PROVIDER_URL") or "http://127.0.0.1:8099").rstrip("/")
    if not op_id or not provider_key:
        return {"status": "failed", "error": "missing operation_id or provider_operation_key", "permanent": True}

    op = await load_operation(op_id)
    if not op:
        return {"status": "failed", "error": "operation_missing", "permanent": True}

    # Provider status (separate query path — not /execute)
    try:
        with urlopen(f"{base}/status/{provider_key}", timeout=5) as resp:
            prov = json.loads(resp.read().decode() or "{}")
    except Exception as e:
        return {"status": "failed", "error": f"provider_status_failed:{e}", "permanent": True}

    if not prov.get("accepted"):
        return {"status": "failed", "error": "provider_not_accepted", "permanent": True, "provider": prov}

    # Persist evidence so reconcile_operation can promote UNKNOWN → SUCCEEDED
    async with AsyncSessionLocal() as db:
        ev = EvidenceRecord(
            id=gen_id(),
            owner_id=op.get("owner_id") or job.owner_id,
            tenant_id=op.get("tenant_id") or job.tenant_id,
            goal="staging_p1_reconcile",
            operation_id=op_id,
            body={
                "operation_id": op_id,
                "provider_operation_key": provider_key,
                "provider": prov,
                "tool": op.get("tool_name") or "staging_p1_external",
                "owner_id": op.get("owner_id"),
                "tenant_id": op.get("tenant_id"),
                "input_digest": op.get("input_digest"),
            },
        )
        db.add(ev)
        await db.commit()
        evidence_id = ev.id

    rec = await reconcile_operation(op_id)
    st = (rec.get("status") or "").lower()
    if st != OP_SUCCEEDED and st != "succeeded":
        # Explicit complete if reconcile left UNKNOWN but provider accepted
        if (op.get("status") == OP_UNKNOWN or st == OP_UNKNOWN) and prov.get("accepted"):
            ok = await complete_operation(op_id, success=True, evidence_id=evidence_id)
            if not ok:
                return {"status": "failed", "error": "complete_operation_failed", "reconcile": rec, "permanent": True}
        else:
            return {"status": "failed", "error": f"reconcile_status:{st}", "reconcile": rec, "permanent": True}

    return {
        "status": "succeeded",
        "reconcile": rec,
        "provider": prov,
        "evidence_id": evidence_id,
        "job_id": job.id,
        "operation_id": op_id,
    }


async def main() -> int:
    from core.database import init_db
    from workers.job_queue import JobWorker, LEASE_S

    await init_db()
    wid = os.environ.get("DEVOS_WORKER_ID") or "worker-unspecified"
    poll = float(os.environ.get("DEVOS_JOB_POLL_S", "0.3"))
    logger.info("starting worker_id=%s lease_s=%s poll_s=%s", wid, LEASE_S, poll)

    worker = JobWorker(poll_s=poll)
    worker.register("staging_p0_hold", _handler_staging_p0_hold)
    worker.register("ping", _handler_ping)
    worker.register("staging_p1_external", _handler_staging_p1_external)
    worker.register("staging_p1_reconcile", _handler_staging_p1_reconcile)

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _on_sig(*_):
        logger.info("signal received — graceful stop")
        worker.stop()
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_sig)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _on_sig())

    task = worker.start()
    await stop.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
