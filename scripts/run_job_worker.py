#!/usr/bin/env python3
"""Standalone ExecutionJob worker process for multi-worker staging.

Usage:
  DEVOS_WORKER_ID=worker-a DEVOS_JOB_LEASE_S=5 \\
    DATABASE_URL=postgresql+asyncpg://... python scripts/run_job_worker.py

Independent OS process — killable with SIGKILL. Not an in-memory MemQueue.
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
    """Hold the claim so the drill can SIGKILL before complete.

    Sleep duration is driven by DEVOS_STAGING_HOLD_S (default 120).
    On normal completion returns succeeded with staging markers.
    """
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

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _on_sig(*_):
        logger.info("signal received — graceful stop requested (SIGKILL bypasses this)")
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
    logger.info("worker exited cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
