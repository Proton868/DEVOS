"""Internal Agent Protocol + capability discovery."""
from __future__ import annotations
import logging, threading
from dataclasses import dataclass, field
from core.event_store import EventStore, ProtocolEvent, ProtocolEventType
from core.task_orch import OrchestrationService
from core.task_contract import TaskRequest, TaskResult, TaskStatus
from core.task_registry import TaskRegistry
logger = logging.getLogger("devos.agent_protocol")

@dataclass
class AgentDescriptor:
    agent_id: str
    worker_slug: str
    name: str
    description: str = ""
    capabilities: list = field(default_factory=list)
    status: str = "available"
    metadata: dict = field(default_factory=dict)
    def __post_init__(self):
        if not self.agent_id or not self.worker_slug: raise ValueError("agent_id and worker_slug required")
        self.capabilities = list(self.capabilities or []); self.metadata = dict(self.metadata or {})
    def to_dict(self):
        return {"agent_id": self.agent_id, "worker_slug": self.worker_slug, "name": self.name,
            "description": self.description, "capabilities": list(self.capabilities),
            "status": self.status, "metadata": self.metadata}
    def satisfies(self, required):
        if not required: return True
        have = set(self.capabilities)
        return all(c in have for c in required)

class AgentRegistry:
    def __init__(self):
        self._agents = {}; self._lock = threading.RLock()
    def register(self, agent: AgentDescriptor):
        with self._lock: self._agents[agent.agent_id] = agent
    def unregister(self, agent_id):
        with self._lock: return self._agents.pop(agent_id, None) is not None
    def get(self, agent_id): return self._agents.get(agent_id)
    def list(self): return sorted(self._agents.values(), key=lambda a: a.agent_id)
    def discover(self, required_capabilities):
        req = list(required_capabilities or [])
        matches = [a for a in self._agents.values() if a.status == "available" and a.satisfies(req)]
        matches.sort(key=lambda a: a.agent_id); return matches
    def select(self, required_capabilities):
        m = self.discover(required_capabilities); return m[0] if m else None
    def bootstrap_from_library(self):
        try:
            from brain.agents import AGENT_LIBRARY
        except Exception: return 0
        ROLE_CAPS = {
            "coder": ["code_generation","code_editing","testing"],
            "engineer": ["code_generation","code_editing","testing"],
            "researcher": ["web_research","document_analysis"],
            "reviewer": ["code_review","security_review","test_analysis"],
            "tester": ["testing","test_analysis"],
            "security": ["security_review"], "architect": ["architecture","planning"],
            "planner": ["planning"], "debugger": ["code_editing","debugging"],
            "devops": ["deployment","infrastructure"], "documenter": ["documentation"],
        }
        n = 0
        for slug, persona in AGENT_LIBRARY.items():
            caps = set()
            for key, rcaps in ROLE_CAPS.items():
                if key in slug.lower() or key in (getattr(persona, "name", "") or "").lower():
                    caps.update(rcaps)
            if not caps: caps.add("general")
            self.register(AgentDescriptor(f"agent:{slug}", slug, getattr(persona, "name", None) or slug,
                description=(getattr(persona, "description", None) or "")[:300],
                capabilities=sorted(caps), metadata={"source": "AGENT_LIBRARY"}))
            n += 1
        return n

class AgentProtocol:
    def __init__(self, agent_registry=None, task_registry=None, event_store=None):
        self.agents = agent_registry or AgentRegistry()
        self.tasks = task_registry or TaskRegistry()
        self.events = event_store or EventStore()
        self.orch = OrchestrationService(registry=self.tasks, events=self.events)
    def cancel(self, task_id, reason="cancelled by caller"):
        return self.orch.cancel_tree(task_id, reason)
    def resume_request(self, task_id):
        return self.orch.resume_request(task_id)

    async def dispatch(self, request, requester_identity, provider=None, model=None, on_step=None, agent=None):
        """Canonical delegation: TaskRequest → registry → WorkerRuntime(task=) → TaskResult."""
        if not isinstance(request, TaskRequest):
            request = TaskRequest.from_dict(request)
        required = list(request.required_capabilities or [])
        if agent is None:
            if request.worker_slug:
                candidates = [a for a in self.agents.list() if a.worker_slug == request.worker_slug]
                if required:
                    candidates = [a for a in candidates if a.satisfies(required)]
                agent = candidates[0] if candidates else None
                if agent is None:
                    from core.agent_protocol import AgentDescriptor
                    agent = AgentDescriptor(
                        agent_id=f"agent:{request.worker_slug}",
                        worker_slug=request.worker_slug,
                        name=request.worker_slug,
                        capabilities=list(required) if required else ["general"],
                    )
                    self.agents.register(agent)
            else:
                agent = self.agents.select(required)
            if agent is None:
                err = f"No agent for capabilities={required} slug={request.worker_slug}"
                result = TaskResult(
                    task_id=request.task_id, execution_id=request.execution_id,
                    worker_slug=request.worker_slug or "unknown", status=TaskStatus.FAILED,
                    errors=[err], failure_reason=err, attempt=request.attempt,
                    parent_task_id=request.parent_task_id, root_loop_id=request.root_loop_id,
                )
                self.tasks.save_result(result)
                return result

        request.worker_slug = agent.worker_slug
        request.agent_id = agent.agent_id
        if not request.root_loop_id:
            request.root_loop_id = request.parent_task_id or request.task_id

        self.tasks.save_request(request)
        if request.parent_task_id:
            self.tasks.link_child(request.parent_task_id, request.task_id)

        for et in (ProtocolEventType.TASK_DISPATCHED, ProtocolEventType.TASK_ACKNOWLEDGED, ProtocolEventType.TASK_STARTED):
            self.events.emit(ProtocolEvent(
                et, request.task_id, request.execution_id,
                agent_id=agent.agent_id, parent_task_id=request.parent_task_id,
                root_task_id=request.root_loop_id,
                payload={"worker_slug": agent.worker_slug},
            ))

        try:
            from workers.runtime import WorkerRuntime, UnknownWorkerError, WorkerTrustUnavailable
            tenant_id = (request.metadata or {}).get("tenant_id") or getattr(requester_identity, "tenant_id", None)
            state, identity, result = await WorkerRuntime().run(
                requester_identity=requester_identity,
                provider=provider,
                model=model,
                on_step=on_step,
                tenant_id=tenant_id,
                owner_id=getattr(requester_identity, "user_id", None),
                task=request,
            )
        except Exception as e:
            logger.exception("dispatch failed")
            result = TaskResult(
                task_id=request.task_id, execution_id=request.execution_id,
                worker_slug=request.worker_slug, status=TaskStatus.FAILED,
                agent_id=agent.agent_id, errors=[str(e)], failure_reason=str(e),
                attempt=request.attempt, parent_task_id=request.parent_task_id,
                root_loop_id=request.root_loop_id,
            )
        self.tasks.save_result(result)
        et = ProtocolEventType.TASK_SUCCEEDED if result.succeeded else (
            ProtocolEventType.TASK_CANCELLED if result.status == TaskStatus.CANCELLED else ProtocolEventType.TASK_FAILED)
        self.events.emit(ProtocolEvent(
            et, request.task_id, request.execution_id, agent_id=agent.agent_id,
            parent_task_id=request.parent_task_id, root_task_id=request.root_loop_id,
            payload={"status": result.status.value},
        ))
        return result
