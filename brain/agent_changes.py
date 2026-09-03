"""
Agent change review store.

When the agent modifies workspace files, we snapshot prior content so the
user can reject/revert without re-running the agent tool.

This is NOT a transactional filesystem. External side effects remain
non-transactional. Snapshots are best-effort, task-scoped, and bounded.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("devos.agent_changes")

_LOCK = threading.RLock()


@dataclass
class ChangeRecord:
    id: str
    task_id: str
    user_id: str
    project_id: str
    path: str
    change_kind: str  # created | patched | replaced | renamed | deleted
    before_content: Optional[str]  # None if file did not exist
    after_content: Optional[str]   # None if deleted
    before_hash: Optional[str]
    after_hash: Optional[str]
    status: str = "applied"  # applied | rejected | reverted
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    meta: dict = field(default_factory=dict)

    def to_dict(self, include_content: bool = False) -> dict:
        d = {
            "id": self.id,
            "task_id": self.task_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "path": self.path,
            "change_kind": self.change_kind,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "status": self.status,
            "created_at": self.created_at,
            "meta": self.meta,
            "has_before": self.before_content is not None,
            "has_after": self.after_content is not None,
        }
        if include_content:
            d["before_content"] = self.before_content
            d["after_content"] = self.after_content
        return d


# task_id -> list[ChangeRecord]
_CHANGES: dict[str, list[ChangeRecord]] = {}
# Bound total records process-wide
_MAX_RECORDS = 2000


def _hash(content: Optional[str]) -> Optional[str]:
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def record_change(
    *,
    task_id: str,
    user_id: str,
    project_id: str,
    path: str,
    change_kind: str,
    before_content: Optional[str],
    after_content: Optional[str],
    meta: Optional[dict] = None,
) -> ChangeRecord:
    rec = ChangeRecord(
        id=str(uuid.uuid4()),
        task_id=task_id,
        user_id=user_id,
        project_id=project_id,
        path=path,
        change_kind=change_kind,
        before_content=before_content,
        after_content=after_content,
        before_hash=_hash(before_content),
        after_hash=_hash(after_content),
        meta=meta or {},
    )
    with _LOCK:
        lst = _CHANGES.setdefault(task_id, [])
        lst.append(rec)
        # Bound per task
        if len(lst) > 200:
            _CHANGES[task_id] = lst[-200:]
        total = sum(len(v) for v in _CHANGES.values())
        if total > _MAX_RECORDS:
            # Drop oldest tasks' first records crudely
            for tid in list(_CHANGES.keys()):
                if sum(len(v) for v in _CHANGES.values()) <= _MAX_RECORDS:
                    break
                if _CHANGES[tid]:
                    _CHANGES[tid].pop(0)
                if not _CHANGES[tid]:
                    del _CHANGES[tid]
    return rec


def list_changes(task_id: str, user_id: str) -> list[dict]:
    with _LOCK:
        recs = [r for r in _CHANGES.get(task_id, []) if r.user_id == user_id]
    return [r.to_dict(include_content=False) for r in recs]


def get_change(change_id: str, user_id: str) -> Optional[ChangeRecord]:
    with _LOCK:
        for recs in _CHANGES.values():
            for r in recs:
                if r.id == change_id and r.user_id == user_id:
                    return r
    return None


def list_task_changes_detailed(task_id: str, user_id: str) -> list[dict]:
    with _LOCK:
        recs = [r for r in _CHANGES.get(task_id, []) if r.user_id == user_id]
    out = []
    for r in recs:
        d = r.to_dict(include_content=False)
        # Provide short unified-style line counts without shipping full content always
        if r.before_content is not None and r.after_content is not None:
            from brain.agent_tools import make_line_diff
            diff = make_line_diff(r.before_content, r.after_content)
            d["additions"] = sum(1 for x in diff if x["type"] == "add")
            d["deletions"] = sum(1 for x in diff if x["type"] == "del")
            d["diff"] = diff[:300]
        elif r.change_kind == "created":
            d["additions"] = (r.after_content or "").count("\n") + (1 if r.after_content else 0)
            d["deletions"] = 0
        elif r.change_kind == "deleted":
            d["additions"] = 0
            d["deletions"] = (r.before_content or "").count("\n") + (1 if r.before_content else 0)
        out.append(d)
    return out


def _disk_matches_after(rec: ChangeRecord, file_service) -> tuple:
    """Return (matches, reason). Refuse silent overwrite when disk diverged."""
    if rec.change_kind == "created":
        try:
            cur = file_service.read(rec.path)
            content = cur.get("content") if isinstance(cur, dict) else None
        except FileNotFoundError:
            return True, "missing"
        if rec.after_hash and _hash(content) != rec.after_hash:
            return False, "stale_content: file changed after agent write"
        return True, "ok"
    if rec.change_kind == "deleted":
        try:
            file_service.read(rec.path)
            return False, "stale_content: file reappeared after agent delete"
        except FileNotFoundError:
            return True, "missing"
        except Exception:
            return True, "ok"
    try:
        cur = file_service.read(rec.path)
        content = cur.get("content") if isinstance(cur, dict) else None
    except FileNotFoundError:
        return False, "stale_content: file missing (expected agent after-state)"
    except Exception as e:
        return False, f"stale_content: read failed ({e})"
    if rec.after_hash and _hash(content) != rec.after_hash:
        return False, "stale_content: concurrent user modification detected"
    return True, "ok"


def revert_change(change_id: str, user_id: str, file_service, *, force: bool = False) -> dict:
    """
    Restore workspace file to before_content using FileService.
    Refuses to overwrite when disk content no longer matches the agent's
    after_hash unless force=True (explicit operator override).
    """
    rec = get_change(change_id, user_id)
    if not rec:
        return {"ok": False, "error": "change not found"}
    if rec.status == "reverted":
        return {"ok": True, "already": True, "path": rec.path}

    if not force:
        matches, reason = _disk_matches_after(rec, file_service)
        if not matches:
            return {
                "ok": False,
                "error": reason,
                "stale": True,
                "path": rec.path,
                "change_id": rec.id,
            }

    try:
        if rec.change_kind == "created":
            try:
                file_service.delete(rec.path)
            except FileNotFoundError:
                pass
        elif rec.change_kind == "deleted":
            if rec.before_content is None:
                return {"ok": False, "error": "no snapshot to restore"}
            try:
                file_service.write(rec.path, rec.before_content)
            except Exception:
                file_service.create(rec.path, is_dir=False)
                file_service.write(rec.path, rec.before_content)
        elif rec.change_kind == "renamed":
            from_path = (rec.meta or {}).get("from") or rec.path
            to_path = (rec.meta or {}).get("to") or rec.path
            try:
                file_service.delete(to_path)
            except Exception:
                pass
            if rec.before_content is not None:
                file_service.write(from_path, rec.before_content)
        else:
            if rec.before_content is None:
                return {"ok": False, "error": "no before snapshot"}
            file_service.write(rec.path, rec.before_content)
        rec.status = "reverted"
        return {"ok": True, "path": rec.path, "status": "reverted", "change_id": rec.id}
    except Exception as e:
        logger.exception("revert failed")
        return {"ok": False, "error": str(e)}


def revert_all(task_id: str, user_id: str, file_service) -> dict:
    with _LOCK:
        recs = [r for r in reversed(_CHANGES.get(task_id, [])) if r.user_id == user_id]
    results = []
    for r in recs:
        if r.status == "reverted":
            continue
        results.append(revert_change(r.id, user_id, file_service))
    return {"ok": True, "results": results}


def accept_change(change_id: str, user_id: str) -> dict:
    """Mark applied change as accepted (already on disk)."""
    rec = get_change(change_id, user_id)
    if not rec:
        return {"ok": False, "error": "change not found"}
    if rec.status == "reverted":
        return {"ok": False, "error": "already reverted; re-apply required"}
    rec.status = "accepted"
    return {"ok": True, "change_id": rec.id, "status": "accepted"}


def accept_all(task_id: str, user_id: str) -> dict:
    with _LOCK:
        recs = [r for r in _CHANGES.get(task_id, []) if r.user_id == user_id and r.status != "reverted"]
    for r in recs:
        r.status = "accepted"
    return {"ok": True, "count": len(recs)}


def reject_change(change_id: str, user_id: str, file_service) -> dict:
    """Reject == revert for applied agent changes."""
    out = revert_change(change_id, user_id, file_service)
    rec = get_change(change_id, user_id)
    if rec and out.get("ok"):
        rec.status = "rejected"
        out["status"] = "rejected"
    return out


def reject_all(task_id: str, user_id: str, file_service) -> dict:
    out = revert_all(task_id, user_id, file_service)
    with _LOCK:
        for r in _CHANGES.get(task_id, []):
            if r.user_id == user_id and r.status == "reverted":
                r.status = "rejected"
    return out
