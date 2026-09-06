"""TaskRequest / TaskResult (transport-neutral)."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

def _now(): return datetime.now(timezone.utc).isoformat()
def _uid(): return str(uuid.uuid4())

class TaskStatus(str, Enum):
    QUEUED="queued"; RUNNING="running"; WAITING_HITL="waiting_hitl"
    WAITING_CHILD="waiting_child"; SUCCEEDED="succeeded"; FAILED="failed"; CANCELLED="cancelled"

TERMINAL = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
INCOMPLETE = {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_HITL, TaskStatus.WAITING_CHILD}

@dataclass
class TaskRequest:
    objective: str
    worker_slug: str
    task_id: str = field(default_factory=_uid)
    execution_id: str = field(default_factory=_uid)
    parent_task_id: Optional[str] = None
    parent_loop_id: Optional[str] = None
    root_loop_id: Optional[str] = None
    agent_id: Optional[str] = None
    attempt: int = 1
    inputs: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    required_capabilities: list = field(default_factory=list)
    timeout_seconds: Optional[int] = None
    expected_result_schema: Optional[dict] = None
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    def __post_init__(self):
        if not (self.objective and str(self.objective).strip()): raise ValueError("objective required")
        if not (self.worker_slug and str(self.worker_slug).strip()): raise ValueError("worker_slug required")
        if self.attempt < 1: raise ValueError("attempt >= 1")
        self.inputs=dict(self.inputs or {}); self.constraints=dict(self.constraints or {})
        self.metadata=dict(self.metadata or {}); self.required_capabilities=list(self.required_capabilities or [])
    def to_dict(self):
        return {k: getattr(self,k) for k in (
            "task_id","execution_id","parent_task_id","parent_loop_id","root_loop_id","agent_id",
            "worker_slug","objective","attempt","inputs","constraints","required_capabilities",
            "timeout_seconds","expected_result_schema","metadata","created_at")}
    @classmethod
    def from_dict(cls, d):
        keys = ("task_id","execution_id","parent_task_id","parent_loop_id","root_loop_id","agent_id",
                "timeout_seconds","expected_result_schema","created_at")
        return cls(**{k:d.get(k) for k in keys}, worker_slug=d["worker_slug"], objective=d["objective"],
                   attempt=int(d.get("attempt") or 1), inputs=dict(d.get("inputs") or {}),
                   constraints=dict(d.get("constraints") or {}),
                   required_capabilities=list(d.get("required_capabilities") or []),
                   metadata=dict(d.get("metadata") or {}))
    def child(self, worker_slug, objective, **kw):
        return TaskRequest(objective=objective, worker_slug=worker_slug, execution_id=self.execution_id,
            parent_task_id=self.task_id, parent_loop_id=kw.get("parent_loop_id", self.parent_loop_id),
            root_loop_id=self.root_loop_id or self.task_id, inputs=kw.get("inputs",{}),
            constraints=kw.get("constraints", dict(self.constraints)),
            required_capabilities=kw.get("required_capabilities",[]),
            timeout_seconds=kw.get("timeout_seconds", self.timeout_seconds), metadata=kw.get("metadata",{}))
    def next_attempt(self):
        d=self.to_dict(); d["attempt"]=int(self.attempt)+1; return TaskRequest.from_dict(d)

@dataclass
class TaskResult:
    task_id: str; execution_id: str; worker_slug: str; status: TaskStatus
    agent_id: Optional[str]=None; output: Any=None
    artifacts: list=field(default_factory=list); errors: list=field(default_factory=list)
    evidence: list=field(default_factory=list); child_tasks: list=field(default_factory=list)
    metadata: dict=field(default_factory=dict); loop_id: Optional[str]=None
    parent_task_id: Optional[str]=None; parent_loop_id: Optional[str]=None
    root_loop_id: Optional[str]=None; attempt: int=1
    started_at: Optional[str]=None; completed_at: Optional[str]=None
    decision: Optional[str]=None; failure_kind: Optional[str]=None; failure_reason: Optional[str]=None
    def __post_init__(self):
        if isinstance(self.status, str): self.status = TaskStatus(self.status)
        self.artifacts=list(self.artifacts or []); self.errors=list(self.errors or [])
        self.evidence=list(self.evidence or []); self.child_tasks=list(self.child_tasks or [])
        self.metadata=dict(self.metadata or {})
    @property
    def succeeded(self): return self.status == TaskStatus.SUCCEEDED
    @property
    def is_terminal(self): return self.status in TERMINAL
    def to_dict(self):
        d={k:getattr(self,k) for k in (
            "task_id","execution_id","agent_id","worker_slug","output","artifacts","errors","evidence",
            "child_tasks","metadata","loop_id","parent_task_id","parent_loop_id","root_loop_id","attempt",
            "started_at","completed_at","decision","failure_kind","failure_reason")}
        d["status"]=self.status.value if isinstance(self.status, Enum) else self.status
        return d
    @classmethod
    def from_dict(cls, d):
        return cls(task_id=d["task_id"], execution_id=d["execution_id"], worker_slug=d["worker_slug"],
            status=TaskStatus(d["status"]), agent_id=d.get("agent_id"), output=d.get("output"),
            artifacts=list(d.get("artifacts") or []), errors=list(d.get("errors") or []),
            evidence=list(d.get("evidence") or []), child_tasks=list(d.get("child_tasks") or []),
            metadata=dict(d.get("metadata") or {}), loop_id=d.get("loop_id"),
            parent_task_id=d.get("parent_task_id"), parent_loop_id=d.get("parent_loop_id"),
            root_loop_id=d.get("root_loop_id"), attempt=int(d.get("attempt") or 1),
            started_at=d.get("started_at"), completed_at=d.get("completed_at"),
            decision=d.get("decision"), failure_kind=d.get("failure_kind"), failure_reason=d.get("failure_reason"))
