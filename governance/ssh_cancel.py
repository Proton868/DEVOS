"""Unified cancellation for SSH agent jobs, exec, transfers, and workflows.

When the user cancels:
1. Stop issuing new commands (is_cancelled checks)
2. Cancel current operation where possible
3. Send termination signals (recorded + transport force close)
4. Close SSH channels
5. Mark execution cancelled
6. Preserve evidence
7. Never report cancellation as success

Cancellation is never reported as success.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.ssh_cancel")

_lock = threading.Lock()
_JOBS: dict[str, dict] = {}
# job_id → transport session cleanup callbacks
_CLEANUPS: dict[str, list] = {}


@dataclass
class CancelState:
    job_id: str
    cancelled: bool = False
    reason: str = ""
    at: Optional[str] = None
    phase: str = "running"  # running|cancelling|cancelled|failed_to_cancel
    evidence_preserved: bool = True


def register_job(
    job_id: str,
    *,
    kind: str = "ssh_agent",
    owner_id: str = "",
    transport: Any = None,
    session_id: Optional[str] = None,
) -> None:
    with _lock:
        existing = _JOBS.get(job_id)
        if existing and existing.get("cancelled"):
            existing["kind"] = kind or existing.get("kind")
            existing["owner_id"] = owner_id or existing.get("owner_id")
            if transport is not None:
                existing["transport"] = transport
            if session_id:
                existing["session_id"] = session_id
            return
        _JOBS[job_id] = {
            "kind": kind,
            "owner_id": owner_id,
            "cancelled": False,
            "reason": "",
            "at": None,
            "phase": "running",
            "signals_sent": [],
            "transport": transport,
            "session_id": session_id,
            "channels_closed": False,
            "evidence_preserved": True,
        }


def bind_transport(job_id: str, transport: Any, session_id: str) -> None:
    with _lock:
        job = _JOBS.setdefault(job_id, {
            "kind": "ssh_agent",
            "owner_id": "",
            "cancelled": False,
            "reason": "",
            "at": None,
            "phase": "running",
            "signals_sent": [],
            "channels_closed": False,
            "evidence_preserved": True,
        })
        job["transport"] = transport
        job["session_id"] = session_id


def register_cleanup(job_id: str, callback) -> None:
    """Register async or sync cleanup (e.g. kill remote process group)."""
    with _lock:
        _CLEANUPS.setdefault(job_id, []).append(callback)


def request_cancel(job_id: str, *, reason: str = "user_cancelled") -> bool:
    """Mark job cancelled, attempt transport terminate, run cleanups."""
    transport = None
    session_id = None
    cleanups = []
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
                "signals_sent": ["cancel_flag"],
                "channels_closed": False,
                "evidence_preserved": True,
            }
            logger.info("ssh_cancel_requested job_id=%s reason=%s (lazy)", job_id, reason)
            job = _JOBS[job_id]
        else:
            job["cancelled"] = True
            job["reason"] = reason
            job["at"] = datetime.now(timezone.utc).isoformat()
            job["phase"] = "cancelling"
            job.setdefault("signals_sent", []).append("cancel_flag")
        transport = job.get("transport")
        session_id = job.get("session_id")
        cleanups = list(_CLEANUPS.get(job_id) or [])
        owner_id = job.get("owner_id") or ""

    logger.info("ssh_cancel_requested job_id=%s reason=%s", job_id, reason)

    # 3–5: terminate signals + close channels
    if transport is not None and session_id:
        try:
            mark_signal_sent(job_id, "SIGTERM_or_channel_close")
            # Prefer force_terminate if available
            closer = getattr(transport, "force_terminate", None) or getattr(transport, "close", None)
            if closer:
                import asyncio
                result = closer(session_id)
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)
                        # also mark dropped immediately for responsiveness
                        mark = getattr(transport, "mark_dropped", None)
                        if mark:
                            mark(session_id, reason="user_cancelled")
                    except RuntimeError:
                        asyncio.run(result)
                with _lock:
                    if job_id in _JOBS:
                        _JOBS[job_id]["channels_closed"] = True
                        _JOBS[job_id].setdefault("signals_sent", []).append("channel_closed")
        except Exception as e:
            logger.warning("ssh_cancel_transport_failed job=%s err=%s", job_id, type(e).__name__)
            with _lock:
                if job_id in _JOBS:
                    _JOBS[job_id]["phase"] = "failed_to_cancel"

    for cb in cleanups:
        try:
            out = cb()
            if hasattr(out, "__await__"):
                pass  # caller event loop may await separately
            mark_signal_sent(job_id, "cleanup_callback")
        except Exception:
            logger.warning("ssh_cancel_cleanup_failed job=%s", job_id)

    try:
        from governance.structured_audit import audit_ssh_cancel
        audit_ssh_cancel(actor_id=owner_id, job_id=job_id, reason=reason)
    except Exception:
        pass

    mark_cancelled(job_id)
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
            if job.get("phase") == "running":
                job["phase"] = "cancelling"


def mark_cancelled(job_id: str) -> None:
    with _lock:
        job = _JOBS.get(job_id)
        if job:
            job["phase"] = "cancelled"
            job["cancelled"] = True
            job["evidence_preserved"] = True


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        j = _JOBS.get(job_id)
        if not j:
            return None
        # strip non-serializable transport
        out = {k: v for k, v in j.items() if k != "transport"}
        return out


def clear_for_tests() -> None:
    with _lock:
        _JOBS.clear()
        _CLEANUPS.clear()


def cancel_status_result(*, job_id: str, evidence_id: str = "") -> dict:
    """Canonical cancelled outcome — never status=succeeded / success=True."""
    job = get_job(job_id) or {}
    return {
        "status": "cancelled",
        "job_id": job_id,
        "reason": job.get("reason") or "user_cancelled",
        "phase": job.get("phase") or "cancelled",
        "signals_sent": list(job.get("signals_sent") or []),
        "channels_closed": bool(job.get("channels_closed")),
        "evidence_id": evidence_id,
        "evidence_preserved": bool(job.get("evidence_preserved", True)),
        "success": False,
    }
