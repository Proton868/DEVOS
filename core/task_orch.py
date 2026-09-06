"""Cancel tree, resume policy."""
from __future__ import annotations
import logging
from core.event_store import EventStore, ProtocolEvent, ProtocolEventType
from core.task_contract import TaskRequest, TaskResult, TaskStatus
from core.task_registry import TaskRegistry
logger = logging.getLogger("devos.task_orch")

class OrchestrationService:
    def __init__(self, registry=None, events=None):
        self.tasks = registry or TaskRegistry()
        self.events = events or EventStore()
        self._running = set()
    def cancel_tree(self, task_id, reason="cancelled"):
        cancelled, stack, seen = [], [task_id], set()
        while stack:
            tid = stack.pop()
            if tid in seen: continue
            seen.add(tid); self._running.discard(tid)
            result = self.tasks.mark_cancelled(tid, reason); cancelled.append(result)
            req = self.tasks.get_request(tid)
            self.events.emit(ProtocolEvent(ProtocolEventType.TASK_CANCELLED, tid,
                (req.execution_id if req else tid), parent_task_id=(req.parent_task_id if req else None),
                root_task_id=(req.root_loop_id if req else tid) or tid,
                agent_id=(req.agent_id if req else None), payload={"reason": reason}))
            try:
                from core.loop import BrainExecutionLoop
                data = self.tasks.get(tid) or {}
                loop_id = data.get("loop_id") or data.get("checkpoint_reference")
                if loop_id and hasattr(BrainExecutionLoop, "cancel_loop"):
                    BrainExecutionLoop.cancel_loop(loop_id, reason)
            except Exception as e: logger.debug(f"cancel_loop: {e}")
            for child in self.tasks.children(tid):
                if child.get("task_id"): stack.append(child["task_id"])
        return cancelled
    def resume_request(self, task_id) -> TaskRequest:
        data = self.tasks.get(task_id)
        if not data: raise ValueError(f"No task {task_id}")
        if data.get("status") == TaskStatus.SUCCEEDED.value: raise ValueError(f"Task {task_id} already succeeded")
        if data.get("cancel_requested") and data.get("status") == TaskStatus.CANCELLED.value:
            raise ValueError(f"Task {task_id} was cancelled; not auto-resumable")
        if task_id in self._running or data.get("status") == TaskStatus.RUNNING.value:
            raise ValueError(f"Task {task_id} already running")
        req = self.tasks.get_request(task_id)
        if not req: raise ValueError(f"No request payload for {task_id}")
        nxt = req.next_attempt()
        self.tasks.save_request(nxt)
        self.tasks.set_status(task_id, TaskStatus.QUEUED, attempt=nxt.attempt)
        self.events.emit(ProtocolEvent(ProtocolEventType.TASK_RESUMED, task_id, nxt.execution_id,
            parent_task_id=nxt.parent_task_id, root_task_id=nxt.root_loop_id or task_id,
            agent_id=nxt.agent_id, payload={"attempt": nxt.attempt}))
        return nxt
