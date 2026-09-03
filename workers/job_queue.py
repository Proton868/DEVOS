"""Multi-node durable job queue: Redis notify + SQL claim (SKIP LOCKED / optimistic).

Lifecycle:
  queued → running → succeeded | failed
  running + lease expired → queued (recoverable, attempts++)

Idempotency: same (tenant, idempotency_key) returns existing non-failed job.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, Awaitable

from sqlalchemy import select, or_, update, and_
from core.database import AsyncSessionLocal, ExecutionJob, gen_id

logger = logging.getLogger("devos.job_queue")
REDIS_URL = os.environ.get("DEVOS_REDIS_URL") or os.environ.get("REDIS_URL") or ""
QUEUE_KEY = os.environ.get("DEVOS_JOB_QUEUE_KEY", "devos:jobs")
LOCK_STALE_S = int(os.environ.get("DEVOS_JOB_LOCK_STALE_S", "300"))
LEASE_S = int(os.environ.get("DEVOS_JOB_LEASE_S", str(LOCK_STALE_S)))


def _worker_id():
    return os.environ.get("DEVOS_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class _RedisBackend:
    def __init__(self, url):
        self.url = url
        self._r = None

    async def connect(self):
        if self._r is not None:
            return self._r
        try:
            import redis.asyncio as redis
            self._r = redis.from_url(self.url, decode_responses=True)
            await self._r.ping()
            return self._r
        except Exception as e:
            logger.warning("Redis unavailable: %s", e)
            self._r = None
            return None

    async def push(self, job_id, priority=100):
        r = await self.connect()
        if r:
            await r.zadd(QUEUE_KEY, {job_id: float(priority)})
            return True
        return False

    async def pop(self):
        r = await self.connect()
        if not r:
            return None
        try:
            items = await r.zpopmin(QUEUE_KEY, count=1)
            return items[0][0] if items else None
        except Exception:
            return None


_redis = _RedisBackend(REDIS_URL) if REDIS_URL else None


async def enqueue(
    *,
    owner_id,
    tenant_id,
    job_type,
    payload,
    actor_id=None,
    priority=100,
    max_attempts=3,
    scheduled_at=None,
    idempotency_key: Optional[str] = None,
    request_id: Optional[str] = None,
    correlation: Optional[dict] = None,
    workflow_id: Optional[str] = None,
    workflow_version: Optional[int] = None,
):
    """Enqueue work. Idempotent when idempotency_key is set for the tenant."""
    from governance.reliability import scrub_secrets
    # Resolve session factory at call time so tests/app can rebind the engine.
    from core import database as _dbmod
    Session = _dbmod.AsyncSessionLocal

    safe_payload = scrub_secrets(payload or {})
    async with Session() as db:
        if idempotency_key and tenant_id:
            r = await db.execute(
                select(ExecutionJob).where(
                    ExecutionJob.tenant_id == tenant_id,
                    ExecutionJob.idempotency_key == idempotency_key,
                    ExecutionJob.status.in_(("queued", "running", "succeeded")),
                ).order_by(ExecutionJob.created_at.desc()).limit(1)
            )
            existing = r.scalar_one_or_none()
            if existing:
                logger.info(
                    "idempotent enqueue hit job=%s key=%s",
                    existing.id, idempotency_key[:12],
                )
                return existing

        # Stage 3M.2 — bind consequential jobs to ExecutionOperation
        NON_CONSEQUENTIAL = frozenset({
            "read", "inspect", "list", "status", "health", "ping",
        })
        op_id = None
        job_id = gen_id()
        jt = (job_type or "").lower()
        is_consequential = jt not in NON_CONSEQUENTIAL and not jt.startswith("read_")
        if is_consequential:
            from governance.execution_operations import reserve_operation
            corr = None
            if isinstance(correlation, dict):
                corr = correlation.get("correlation_id") or correlation.get("id")
            op_id = await reserve_operation(
                owner_id=owner_id,
                tenant_id=tenant_id,
                actor_id=actor_id or owner_id,
                execution_job_id=job_id,
                operation_type=f"job:{jt}",
                tool_name=jt,
                correlation_id=corr,
                idempotency_key=idempotency_key,
                args=safe_payload if isinstance(safe_payload, dict) else {},
            )
            if not op_id:
                raise RuntimeError("operation_reservation_failed: consequential job requires operation")
            safe_payload = dict(safe_payload or {})
            safe_payload["operation_id"] = op_id
        job = ExecutionJob(
            id=job_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            actor_id=actor_id,
            job_type=job_type,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            payload=safe_payload,
            status="queued",
            priority=priority,
            max_attempts=max_attempts,
            scheduled_at=scheduled_at,
            idempotency_key=idempotency_key,
            request_id=request_id,
            correlation=scrub_secrets(correlation or {}),
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        jid, pri = job.id, job.priority

    if _redis and not scheduled_at:
        await _redis.push(jid, pri)
    from governance.failure_injection import maybe_crash
    maybe_crash("after_job_create")
    return job


async def recover_stale_leases(db=None) -> int:
    """Re-queue jobs whose worker lease expired (worker crash / reboot)."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=LOCK_STALE_S)

    async def _do(session):
        from sqlalchemy import select
        # Prefer lease_expires_at when set; fall back to locked_at age
        q = await session.execute(
            select(ExecutionJob).where(
                ExecutionJob.status == "running",
                or_(
                    and_(
                        ExecutionJob.lease_expires_at.is_not(None),
                        ExecutionJob.lease_expires_at < now,
                    ),
                    and_(
                        ExecutionJob.lease_expires_at.is_(None),
                        ExecutionJob.locked_at.is_not(None),
                        ExecutionJob.locked_at < stale,
                    ),
                ),
            )
        )
        jobs = list(q.scalars().all())
        recovered = 0
        for job in jobs:
            # Stage 3M: operation-aware stale lease handling
            op_id = None
            payload = job.payload if isinstance(job.payload, dict) else {}
            op_id = payload.get("operation_id") or (job.correlation or {}).get("operation_id") if isinstance(job.correlation, dict) else payload.get("operation_id")
            if op_id:
                try:
                    from governance.execution_operations import load_operation, reconcile_operation, OP_UNKNOWN, OP_SUCCEEDED, OP_RUNNING
                    op = await load_operation(op_id)
                    if op and op.get("status") in (OP_RUNNING, "reserved"):
                        rec = await reconcile_operation(op_id)
                        # After reconcile, do not requeue UNKNOWN/succeeded
                        st = (rec.get("status") or "").lower()
                        if st in ("unknown", "succeeded", "cancelled"):
                            job.status = "failed" if st == "unknown" else ("succeeded" if st == "succeeded" else "failed")
                            job.error = json.dumps({
                                "reason_code": "operation_unknown" if st == "unknown" else f"operation_{st}",
                                "operation_id": op_id,
                                "retryable": False,
                            })
                            job.worker_id = None
                            job.locked_at = None
                            job.lease_expires_at = None
                            recovered += 1
                            continue
                    if op and op.get("status") == OP_SUCCEEDED:
                        job.status = "succeeded"
                        job.worker_id = None
                        job.locked_at = None
                        job.lease_expires_at = None
                        recovered += 1
                        continue
                except Exception:
                    logger.debug("operation-aware lease recovery failed", exc_info=True)
            # Stage 3M.2 — missing operation on potentially consequential job: fail closed
            jt = (job.job_type or "").lower()
            NON_CONSEQUENTIAL = frozenset({"read", "inspect", "list", "status", "health", "ping"})
            is_consequential = jt not in NON_CONSEQUENTIAL and not jt.startswith("read_")
            if is_consequential and not op_id:
                job.status = "failed"
                job.error = json.dumps({
                    "reason_code": "missing_operation",
                    "retryable": False,
                })
                job.worker_id = None
                job.locked_at = None
                job.lease_expires_at = None
                recovered += 1
                continue
            # Safe non-consequential: existing requeue
            job.status = "queued"
            job.worker_id = None
            job.locked_at = None
            job.lease_expires_at = None
            recovered += 1
        await session.commit()
        return recovered

    if db is not None:
        return await _do(db)
    async with AsyncSessionLocal() as session:
        n = await _do(session)
        if n:
            logger.warning("recovered %s stale job lease(s)", n)
        return n


async def _claim_sql(db, worker):
    await recover_stale_leases(db)
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=LEASE_S)
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    try:
        q = select(ExecutionJob).where(
            ExecutionJob.status == "queued",
            or_(ExecutionJob.scheduled_at.is_(None), ExecutionJob.scheduled_at <= now),
        ).order_by(ExecutionJob.priority.asc(), ExecutionJob.created_at.asc()).limit(1)
        if dialect == "postgresql":
            q = q.with_for_update(skip_locked=True)
        r = await db.execute(q)
        job = r.scalar_one_or_none()
        if not job:
            return None
        result = await db.execute(
            update(ExecutionJob).where(
                ExecutionJob.id == job.id,
                ExecutionJob.status == "queued",
            ).values(
                status="running",
                worker_id=worker,
                locked_at=now,
                lease_expires_at=lease_until,
                started_at=now,
                attempts=ExecutionJob.attempts + 1,
            )
        )
        await db.commit()
        if result.rowcount == 0:
            return None
        await db.refresh(job)
        from governance.failure_injection import maybe_crash
        maybe_crash("after_job_claim")
        return job
    except Exception as e:
        logger.warning("claim failed: %s", e)
        await db.rollback()
        return None


async def claim_next(worker=None):
    """Exactly one worker owns a job after successful claim."""
    wid = worker or _worker_id()
    if _redis:
        jid = await _redis.pop()
        if jid:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(ExecutionJob).where(
                        ExecutionJob.id == jid,
                        ExecutionJob.status == "queued",
                    )
                )
                job = r.scalar_one_or_none()
                if job:
                    now = datetime.now(timezone.utc)
                    # Optimistic single-owner claim
                    result = await db.execute(
                        update(ExecutionJob).where(
                            ExecutionJob.id == job.id,
                            ExecutionJob.status == "queued",
                        ).values(
                            status="running",
                            worker_id=wid,
                            locked_at=now,
                            lease_expires_at=now + timedelta(seconds=LEASE_S),
                            started_at=now,
                            attempts=(job.attempts or 0) + 1,
                        )
                    )
                    await db.commit()
                    if result.rowcount == 1:
                        await db.refresh(job)
                        from governance.failure_injection import maybe_crash
                        maybe_crash("after_job_claim")
                        return job
    async with AsyncSessionLocal() as db:
        return await _claim_sql(db, wid)


async def heartbeat(job_id: str, worker: Optional[str] = None) -> bool:
    """Extend lease while work is in progress."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        q = update(ExecutionJob).where(
            ExecutionJob.id == job_id,
            ExecutionJob.status == "running",
        )
        if worker:
            q = q.where(ExecutionJob.worker_id == worker)
        result = await db.execute(
            q.values(
                locked_at=now,
                lease_expires_at=now + timedelta(seconds=LEASE_S),
            )
        )
        await db.commit()
        return (result.rowcount or 0) == 1


async def complete(job_id, *, status, result=None, error=None, isolation=None):
    """Mark job finished. Idempotent for terminal success — never reopens succeeded jobs."""
    from governance.reliability import scrub_secrets

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(ExecutionJob).where(ExecutionJob.id == job_id))
        job = r.scalar_one_or_none()
        if not job:
            return
        # Terminal success is final (crash-retry must not rewrite outcomes)
        if job.status == "succeeded":
            logger.info("complete ignored for already-succeeded job %s", job_id)
            return
        job.result = scrub_secrets(result) if result is not None else job.result
        job.error = (error or "")[:4000] if error else job.error
        job.isolation = isolation if isolation is not None else job.isolation
        job.finished_at = datetime.now(timezone.utc)
        job.worker_id = None
        job.locked_at = None
        job.lease_expires_at = None
        if status == "failed" and (job.attempts or 0) < (job.max_attempts or 3):
            job.status = "queued"
            job.finished_at = None
            if _redis:
                await _redis.push(job.id, job.priority or 100)
        else:
            job.status = status
        await db.commit()


class JobWorker:
    def __init__(self, handlers=None, poll_s=0.5):
        self.handlers = handlers or {}
        self.poll_s = poll_s
        self._stop = asyncio.Event()
        self.worker_id = _worker_id()

    def register(self, job_type, handler):
        self.handlers[job_type] = handler

    async def run_once(self):
        job = await claim_next(self.worker_id)
        if not job:
            return False
        handler = self.handlers.get(job.job_type)
        if not handler:
            await complete(job.id, status="failed", error=f"no handler for {job.job_type}")
            return True
        try:
            result = await handler(job)
            status = result.get("status", "succeeded")
            # Permanent failures (governance deny, validation, UNKNOWN after interrupt)
            # must not bounce through the transient retry queue.
            if result.get("permanent") and status == "failed":
                async with AsyncSessionLocal() as db:
                    r = await db.execute(select(ExecutionJob).where(ExecutionJob.id == job.id))
                    j = r.scalar_one_or_none()
                    if j:
                        j.max_attempts = j.attempts or 1
                        await db.commit()
            await complete(
                job.id,
                status=status,
                result=result,
                error=result.get("error"),
                isolation=result.get("isolation"),
            )
        except Exception as e:
            logger.exception("job %s failed", job.id)
            await complete(job.id, status="failed", error=str(e))
        return True

    async def loop(self):
        while not self._stop.is_set():
            if not await self.run_once():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_s)
                except asyncio.TimeoutError:
                    pass

    def start(self):
        self._stop.clear()
        return asyncio.create_task(self.loop())

    def stop(self):
        self._stop.set()
