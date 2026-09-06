"""Durable TaskRegistry with attempt history."""
from __future__ import annotations
import logging, threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from core.atomic_io import atomic_write_json, read_json
from core.task_contract import TaskRequest, TaskResult, TaskStatus

logger = logging.getLogger("devos.task_registry")
try:
    from core.config import DATA_DIR
    TASK_DIR = DATA_DIR / "tasks"
except Exception:
    TASK_DIR = Path("data/tasks")
TASK_DIR.mkdir(parents=True, exist_ok=True)

def _now(): return datetime.now(timezone.utc).isoformat()

class TaskRegistry:
    _lock = threading.RLock()
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else TASK_DIR
        self.root.mkdir(parents=True, exist_ok=True)
    def _path(self, tid): return self.root / f"{tid}.json"
    def _load(self, tid): return read_json(self._path(tid))
    def _save(self, tid, data):
        data["task_id"] = tid; data["updated_at"] = _now()
        atomic_write_json(self._path(tid), data)
    def get(self, task_id): return self._load(task_id)
    def save_request(self, req: TaskRequest):
        with self._lock:
            data = self._load(req.task_id) or {}
            data["request"] = req.to_dict()
            data.setdefault("status", TaskStatus.QUEUED.value)
            data.setdefault("created_at", req.created_at)
            for k in ("objective","worker_slug","agent_id","execution_id","attempt","parent_task_id","parent_loop_id","root_loop_id"):
                data[k] = getattr(req, k)
            attempts = list(data.get("attempts") or [])
            if not any(a.get("attempt") == req.attempt for a in attempts):
                attempts.append({"attempt": req.attempt, "status": TaskStatus.QUEUED.value,
                    "started_at": None, "completed_at": None, "error": None, "checkpoint_reference": None})
            data["attempts"] = attempts
            self._save(req.task_id, data)
    def save_result(self, result: TaskResult):
        with self._lock:
            data = self._load(result.task_id) or {"task_id": result.task_id}
            data["result"] = result.to_dict()
            data["status"] = result.status.value if isinstance(result.status, TaskStatus) else result.status
            for k in ("execution_id","agent_id","worker_slug","attempt","parent_task_id","root_loop_id","loop_id"):
                data[k] = getattr(result, k)
            data["checkpoint_reference"] = result.loop_id
            data["error"] = (result.errors[0] if result.errors else result.failure_reason)
            if result.started_at: data["started_at"] = result.started_at
            if result.completed_at: data["completed_at"] = result.completed_at
            attempts = list(data.get("attempts") or [])
            found = False
            for a in attempts:
                if a.get("attempt") == result.attempt:
                    a.update({"status": data["status"], "completed_at": result.completed_at,
                              "error": data.get("error"), "checkpoint_reference": result.loop_id})
                    if result.started_at: a["started_at"] = result.started_at
                    found = True; break
            if not found:
                attempts.append({"attempt": result.attempt, "status": data["status"],
                    "started_at": result.started_at, "completed_at": result.completed_at,
                    "error": data.get("error"), "checkpoint_reference": result.loop_id})
            data["attempts"] = attempts
            self._save(result.task_id, data)
    def set_status(self, task_id, status: TaskStatus, **extra):
        with self._lock:
            data = self._load(task_id) or {"task_id": task_id}
            data["status"] = status.value if isinstance(status, TaskStatus) else status
            data.update(extra); self._save(task_id, data)
    def set_cancel_intent(self, task_id, reason):
        with self._lock:
            data = self._load(task_id) or {"task_id": task_id}
            data["cancel_requested"] = True; data["cancel_reason"] = reason
            data["status"] = TaskStatus.CANCELLED.value; self._save(task_id, data)
    def get_request(self, task_id):
        d = self._load(task_id)
        return TaskRequest.from_dict(d["request"]) if d and "request" in d else None
    def get_result(self, task_id):
        d = self._load(task_id)
        return TaskResult.from_dict(d["result"]) if d and "result" in d else None
    def get_attempts(self, task_id):
        return list((self._load(task_id) or {}).get("attempts") or [])
    def children(self, task_id):
        out = []
        for f in self.root.glob("*.json"):
            if f.name.endswith(".tmp"): continue
            data = read_json(f)
            if not data: continue
            if (data.get("request") or {}).get("parent_task_id") == task_id or data.get("parent_task_id") == task_id:
                out.append(data)
        return sorted(out, key=lambda d: d.get("created_at") or "")
    def ancestors(self, task_id):
        chain, seen, cur = [], set(), task_id
        while cur and cur not in seen:
            seen.add(cur); data = self._load(cur)
            if not data: break
            parent = (data.get("request") or {}).get("parent_task_id") or data.get("parent_task_id")
            if not parent: break
            chain.append(parent); cur = parent
        return chain
    def tree(self, root_task_id):
        data = self._load(root_task_id) or {"task_id": root_task_id}
        kids = [self.tree(c["task_id"]) for c in self.children(root_task_id) if c.get("task_id")]
        return {"task_id": root_task_id, "request": data.get("request"), "result": data.get("result"),
                "status": data.get("status"), "attempts": data.get("attempts"), "children": kids}
    def link_child(self, parent_task_id, child_task_id):
        with self._lock:
            parent = self._load(parent_task_id)
            if not parent: return
            result = parent.get("result") or {"task_id": parent_task_id,
                "execution_id": parent.get("execution_id") or parent_task_id,
                "worker_slug": parent.get("worker_slug") or "unknown",
                "status": parent.get("status") or "running", "child_tasks": []}
            kids = list(result.get("child_tasks") or [])
            if child_task_id not in kids: kids.append(child_task_id)
            result["child_tasks"] = kids; parent["result"] = result; self._save(parent_task_id, parent)
    def mark_cancelled(self, task_id, reason="cancelled"):
        req = self.get_request(task_id)
        result = TaskResult(task_id=task_id, execution_id=(req.execution_id if req else task_id),
            worker_slug=(req.worker_slug if req else "unknown"), status=TaskStatus.CANCELLED,
            failure_reason=reason, attempt=(req.attempt if req else 1),
            parent_task_id=(req.parent_task_id if req else None),
            root_loop_id=(req.root_loop_id if req else None), completed_at=_now())
        self.set_cancel_intent(task_id, reason); self.save_result(result); return result
    def list_all(self):
        return [d for f in self.root.glob("*.json") if not f.name.endswith(".tmp") and (d := read_json(f))]
    def integrity_report(self):
        issues = []
        for data in self.list_all():
            tid = data.get("task_id"); req = data.get("request") or {}
            parent = req.get("parent_task_id") or data.get("parent_task_id")
            if parent and not self._load(parent):
                issues.append({"task_id": tid, "issue": "orphan_child", "parent_task_id": parent})
            if data.get("status") == TaskStatus.RUNNING.value and not (data.get("execution_id") or req.get("execution_id")):
                issues.append({"task_id": tid, "issue": "running_without_execution_id"})
        return issues
