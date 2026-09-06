"""Durable protocol event store."""
from __future__ import annotations
import json, logging, threading, uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("devos.event_store")
try:
    from core.config import DATA_DIR
    EVENT_DIR = DATA_DIR / "events"
except Exception:
    EVENT_DIR = Path("data/events")
EVENT_DIR.mkdir(parents=True, exist_ok=True)

def _now(): return datetime.now(timezone.utc).isoformat()
def _uid(): return str(uuid.uuid4())

class ProtocolEventType(str, Enum):
    TASK_DISPATCHED="task.dispatched"; TASK_ACKNOWLEDGED="task.acknowledged"
    TASK_STARTED="task.started"; TASK_PROGRESS="task.progress"
    TASK_WAITING_HITL="task.waiting_hitl"; TASK_WAITING_CHILD="task.waiting_child"
    TASK_SUCCEEDED="task.succeeded"; TASK_FAILED="task.failed"
    TASK_CANCELLED="task.cancelled"; TASK_CHILD_CREATED="task.child_created"
    TASK_RESUMED="task.resumed"

@dataclass
class ProtocolEvent:
    event_type: ProtocolEventType
    task_id: str
    execution_id: str
    agent_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    root_task_id: Optional[str] = None
    event_id: str = field(default_factory=_uid)
    timestamp: str = field(default_factory=_now)
    payload: dict = field(default_factory=dict)
    def __post_init__(self):
        if isinstance(self.event_type, str): self.event_type = ProtocolEventType(self.event_type)
        self.payload = dict(self.payload or {})
    def to_dict(self):
        return {"event_id": self.event_id,
            "event_type": self.event_type.value if isinstance(self.event_type, Enum) else self.event_type,
            "task_id": self.task_id, "execution_id": self.execution_id,
            "parent_task_id": self.parent_task_id, "root_task_id": self.root_task_id,
            "agent_id": self.agent_id, "timestamp": self.timestamp, "payload": self.payload}
    @classmethod
    def from_dict(cls, d):
        return cls(event_id=d.get("event_id") or _uid(), event_type=ProtocolEventType(d["event_type"]),
            task_id=d["task_id"], execution_id=d["execution_id"], parent_task_id=d.get("parent_task_id"),
            root_task_id=d.get("root_task_id"), agent_id=d.get("agent_id"),
            timestamp=d.get("timestamp") or _now(), payload=dict(d.get("payload") or {}))

class EventStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else EVENT_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._subscribers = []
    def _path(self, task_id): return self.root / f"{task_id}.jsonl"
    def emit(self, event: ProtocolEvent) -> ProtocolEvent:
        with self._lock:
            path = self._path(event.task_id)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), default=str) + "\n"); f.flush()
            subs = list(self._subscribers)
        for fn in subs:
            try: fn(event)
            except Exception as e: logger.debug(f"subscriber error: {e}")
        return event
    def for_task(self, task_id: str):
        path = self._path(task_id)
        if not path.exists(): return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line: continue
            try: out.append(ProtocolEvent.from_dict(json.loads(line)))
            except Exception: continue
        return out
    def subscribe(self, fn): self._subscribers.append(fn)
