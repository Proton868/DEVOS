"""Pure state machine for job lifecycle drills (no DB required).

Mirrors workers/job_queue semantics for failure injection and race tests:
  queued → running (claim, single owner)
  running + lease expired → queued
  running → succeeded (terminal)
  running → failed → queued if attempts < max else failed
  succeeded is terminal (complete ignored)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


@dataclass
class MemJob:
    id: str
    status: str = "queued"
    worker_id: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 3
    locked_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass
class MemQueue:
    jobs: dict[str, MemJob] = field(default_factory=dict)
    lease_s: int = 300

    def enqueue(self, job_id: str, *, idempotency_key: Optional[str] = None) -> MemJob:
        if idempotency_key:
            for j in self.jobs.values():
                if j.idempotency_key == idempotency_key and j.status in (
                    "queued", "running", "succeeded",
                ):
                    return j
        job = MemJob(id=job_id, idempotency_key=idempotency_key)
        self.jobs[job_id] = job
        return job

    def recover_stale(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        n = 0
        for j in self.jobs.values():
            if j.status == "running" and j.lease_expires_at and j.lease_expires_at < now:
                j.status = "queued"
                j.worker_id = None
                j.locked_at = None
                j.lease_expires_at = None
                n += 1
        return n

    def claim(self, worker_id: str, now: Optional[datetime] = None) -> Optional[MemJob]:
        now = now or datetime.now(timezone.utc)
        self.recover_stale(now)
        for j in sorted(self.jobs.values(), key=lambda x: x.id):
            if j.status != "queued":
                continue
            # single-owner optimistic claim
            j.status = "running"
            j.worker_id = worker_id
            j.locked_at = now
            j.lease_expires_at = now + timedelta(seconds=self.lease_s)
            j.attempts += 1
            return j
        return None

    def complete(self, job_id: str, status: str, *, result=None, error=None) -> bool:
        j = self.jobs.get(job_id)
        if not j:
            return False
        if j.status == "succeeded":
            return False  # terminal — ignore
        j.worker_id = None
        j.locked_at = None
        j.lease_expires_at = None
        if status == "failed" and j.attempts < j.max_attempts:
            j.status = "queued"
            j.error = error
            return True
        j.status = status
        j.result = result
        j.error = error
        return True
