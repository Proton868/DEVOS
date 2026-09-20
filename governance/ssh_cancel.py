"""Unified cancellation for SSH agent jobs, exec, transfers, and workflows.

Cancellation is never reported as success.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("devos.ssh_cancel")

_lock = threading.Lock()
_JOBS: dict[str, dict] = {}


@dataclass
class CancelState:
    job_id: str
    cancelled: bool = False
    reason: str = ""
    at: Optional[str] = None
    phase: str = "running"  # running|cancelling|cancelled|failed_to_cancel
    evidence_preserved: bool = True


def register_job(job_id: str, *, kind: str = "ssh_agent", owner_id: str = "") -> None:
    with _lock:
        existing = _JOBS.get(job_id)
        if existing and existing.get("cancelled"):
            # Preserve prior cancel request (e.g. cancel before workflow start)
            existing["kind"] = kind or existing.get("kind")
            existing["owner_id"] = owner_id or existing.get("owner_id")
            return
        _JOBS[job_id] = {
            "kind": kind,
            "owner_id": owner_id,
            "cancelled": False,
            "reason": "",
            "at": None,
            "phase": "running",
            "signals_sent": [],
        }


def request_cancel(job_id: str, *, reason: str = "user_cancelled") -> bool:
    """Mark job cancelled. Callers must stop issuing new commands."""
    with _lock:
        job = _JOBS.get(job_id)
        if not job:
            _JOBS[job_id] = {
                "kind": "unknown",
                "owner_id": "",
                "cancelled": True,
                "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(),
                "phase": "cancelling",
                "signals_sent": [],
            }
            logger.info("ssh_cancel_requested job_id=%s reason=%s (lazy)", job_id, reason)
            return True
        job["cancelled"] = True
        job["reason"] = reason
        job["at"] = datetime.now(timezone.utc).isoformat()
        job["phase"] = "cancelling"
        logger.info("ssh_cancel_requested job_id=%s reason=%s", job_id, reason)
        return True


def is_cancelled(job_id: Optional[str]) -> bool:
    if not job_id:
        return False
    with _lock:
        job = _JOBS.get(job_id)
        return bool(job and job.get("cancelled"))


def mark_signal_sent(job_id: str, signal: str) -> None:
    with _lock:
        job = _JOBS.get(job_id)
        if job:
            job.setdefault("signals_sent", []).append(signal)
            job["phase"] = "cancelling"


def mark_cancelled(job_id: str) -> None:
    with _lock:
        job = _JOBS.get(job_id)
        if job:
            job["phase"] = "cancelled"
            job["cancelled"] = True


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        j = _JOBS.get(job_id)
        return dict(j) if j else None


def clear_for_tests() -> None:
    with _lock:
        _JOBS.clear()


def cancel_status_result(*, job_id: str, evidence_id: str = "") -> dict:
    """Canonical cancelled outcome — never status=succeeded."""
    job = get_job(job_id) or {}
    return {
        "status": "cancelled",
        "job_id": job_id,
        "reason": job.get("reason") or "user_cancelled",
        "phase": job.get("phase") or "cancelled",
        "evidence_id": evidence_id,
        "success": False,  # explicit: cancellation is not success
    }
