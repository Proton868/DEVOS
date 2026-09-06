"""
Thin boundary: orchestration node → existing Agent Runtime.

Not an executor. Submits work, observes results, maps to node outcomes.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("devos.orchestration_runtime")


@dataclass
class NodeExecutionRequest:
    plan_id: str
    node_id: str
    user_id: str
    workspace_id: str
    persona_id: str
    objective: str
    effective_caps: list[str] = field(default_factory=list)
    authorization_decision: str = "allow"
    authorization_fingerprint: str = ""
    # Web Intelligence (optional — set by Mission Engine for web_crawl nodes)
    node_kind: str = ""
    root_url: str = ""
    crawl_id: str = ""
    job_id: str = ""
    force_refresh: bool = False


@dataclass
class NodeExecutionResult:
    success: bool
    task_id: Optional[str] = None
    status: str = "unknown"  # succeeded|failed|cancelled|blocked|error
    summary: str = ""
    files_changed: list = field(default_factory=list)
    error: Optional[str] = None
    events_seen: list[str] = field(default_factory=list)
    raw_terminal: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "files_changed": list(self.files_changed or []),
            "error": self.error,
            "events_seen": list(self.events_seen or []),
        }


def _fingerprint(req: NodeExecutionRequest) -> str:
    import hashlib
    raw = f"{req.plan_id}:{req.node_id}:{req.workspace_id}:{sorted(req.effective_caps)}:{req.objective[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def run_node_on_agent_runtime(req: NodeExecutionRequest) -> NodeExecutionResult:
    """
    Submit one node to AgentRuntime and consume the event stream until terminal.

    Deterministic test path when DEVOS_ORCH_FAKE_RUNTIME=1 (not for production).
    """
    if req.authorization_decision != "allow":
        return NodeExecutionResult(
            success=False, status="blocked",
            error="execution refused: not authorized",
        )
    if not req.user_id:
        return NodeExecutionResult(success=False, status="error", error="missing user_id")
    if not req.workspace_id:
        return NodeExecutionResult(success=False, status="error", error="missing workspace_id")

    # Stale auth protection: fingerprint must match stored if provided
    expected_fp = _fingerprint(req)
    if req.authorization_fingerprint and req.authorization_fingerprint != expected_fp:
        return NodeExecutionResult(
            success=False, status="blocked",
            error="authorization drift: request no longer matches authorized operation",
        )



    # --- Canonical durable task identity (AgentProtocol correlation) ---
    task_req = None
    try:
        from core.task_contract import TaskRequest, TaskResult, TaskStatus
        from core.task_registry import TaskRegistry
        from core.event_store import EventStore, ProtocolEvent, ProtocolEventType
        task_req = TaskRequest(
            objective=req.objective,
            worker_slug=req.persona_id or "agent",
            required_capabilities=list(req.effective_caps or []),
            metadata={
                "plan_id": req.plan_id,
                "node_id": req.node_id,
                "user_id": req.user_id,
                "workspace_id": req.workspace_id,
                "source": "mission_node",
            },
        )
        # Stable correlation with mission node
        task_req.task_id = f"node:{req.plan_id}:{req.node_id}"
        task_req.execution_id = f"node:{req.plan_id}:{req.node_id}:{task_req.attempt}"
        task_req.root_loop_id = f"plan:{req.plan_id}"
        TaskRegistry().save_request(task_req)
        store = EventStore()
        for et in (ProtocolEventType.TASK_DISPATCHED, ProtocolEventType.TASK_ACKNOWLEDGED, ProtocolEventType.TASK_STARTED):
            store.emit(ProtocolEvent(
                et, task_req.task_id, task_req.execution_id,
                agent_id=f"agent:{task_req.worker_slug}",
                root_task_id=task_req.root_loop_id,
                payload={"plan_id": req.plan_id, "node_id": req.node_id},
            ))
    except Exception as e:
        logger.debug("durable task mirror at node start failed: %s", e)

    # --- Web Intelligence path: existing Jobs + crawler (not Agent Runtime tools) ---
    caps = {c.lower() for c in (req.effective_caps or [])}
    kind = (req.node_kind or "").lower()
    is_web = (
        kind in ("web_crawl", "web.intelligence")
        or "web.intelligence" in caps
        or "web_crawl" in (req.objective or "").lower()
        or (req.root_url or "").startswith("http")
    )
    if is_web and (req.root_url or req.crawl_id or "web.intelligence" in caps or kind == "web_crawl"):
        return await _run_web_crawl_node(req)

    # Fake runtime is TEST-ONLY. Never silently fall back in production.
    # Require both FAKE flag and explicit test allow (pytest sets PYTEST_CURRENT_TEST).
    if os.environ.get("DEVOS_ORCH_FAKE_RUNTIME") == "1":
        if os.environ.get("DEVOS_ALLOW_FAKE_RUNTIME") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
            return await _fake_runtime(req)
        return NodeExecutionResult(
            success=False,
            status="error",
            error="AGENT_RUNTIME_UNAVAILABLE: DEVOS_ORCH_FAKE_RUNTIME set without test allow",
        )


    # --- Canonical path: TaskRequest → AgentProtocol → WorkerRuntime → Loop ---
    return await _run_node_via_agent_protocol(req)


async def _run_node_via_agent_protocol(req: NodeExecutionRequest) -> NodeExecutionResult:
    """Executable mission nodes use the single delegation contract."""
    import uuid
    from core.task_contract import TaskRequest, TaskStatus
    from core.agent_protocol import AgentProtocol, AgentRegistry
    from governance.ucip import AgentIdentity, TrustLevel

    worker_slug = (req.persona_id or "fullstack-engineer").strip() or "fullstack-engineer"
    task_req = TaskRequest(
        objective=(
            f"[orch plan={req.plan_id} node={req.node_id} persona={req.persona_id}]\n"
            f"Workspace: {req.workspace_id}\n"
            f"Authorized caps (canonical): {', '.join(req.effective_caps) or 'none'}\n"
            f"{req.objective}"
        ),
        worker_slug=worker_slug,
        required_capabilities=list(req.effective_caps or []),
        metadata={
            "plan_id": req.plan_id,
            "node_id": req.node_id,
            "user_id": req.user_id,
            "workspace_id": req.workspace_id,
            "source": "mission_node",
            # Prefer user_id as tenant fallback for mission trust resolution
            "tenant_id": req.user_id,
        },
    )
    # Stable correlation with mission node (same task across retries via attempt)
    task_req.task_id = f"node:{req.plan_id}:{req.node_id}"
    task_req.execution_id = f"node:{req.plan_id}:{req.node_id}:{task_req.attempt}"
    task_req.root_loop_id = f"plan:{req.plan_id}"

    session_id = str(uuid.uuid4())
    identity = AgentIdentity.create(req.user_id, session_id, TrustLevel.OPERATOR)

    protocol = AgentProtocol(agent_registry=AgentRegistry())
    try:
        protocol.agents.bootstrap_from_library()
    except Exception:
        pass

    try:
        task_result = await protocol.dispatch(task_req, identity)
    except Exception as e:
        logger.exception("mission node protocol dispatch failed")
        return NodeExecutionResult(
            success=False,
            status="error",
            task_id=task_req.task_id,
            error=f"protocol_dispatch:{type(e).__name__}:{e}",
            events_seen=["task.failed"],
        )

    status = task_result.status.value if hasattr(task_result.status, "value") else str(task_result.status)
    success = status == "succeeded"
    if status == "cancelled":
        mapped = "cancelled"
    elif success:
        mapped = "succeeded"
    else:
        mapped = "failed"

    return NodeExecutionResult(
        success=success,
        task_id=task_result.task_id,
        status=mapped,
        summary=str(task_result.output or "")[:2000],
        error=(task_result.errors[0] if task_result.errors else task_result.failure_reason),
        events_seen=[f"task.{status}"],
        raw_terminal=task_result.to_dict() if hasattr(task_result, "to_dict") else None,
    )



async def _fake_runtime(req: NodeExecutionRequest) -> NodeExecutionResult:
    """Deterministic harness only — creates a minimal artifact for website goals."""
    import uuid
    task_id = f"fake-{uuid.uuid4().hex[:12]}"
    files: list[dict] = []
    goal = req.objective.lower()
    try:
        from execution.files import FileService
        fs = FileService(req.user_id, req.workspace_id or "default")
        if any(k in goal for k in ("website", "shoe", "page", "landing")):
            html = (
                "<!DOCTYPE html><html><head><title>Shoes</title></head>"
                "<body><h1>Shoes</h1><p>One-page shoe website (test harness).</p></body></html>"
            )
            path = "index.html"
            if hasattr(fs, "write"):
                maybe = fs.write(path, html)
                if hasattr(maybe, "__await__"):
                    await maybe
            files.append({"path": path, "kind": "created"})
        else:
            path = "orch_result.txt"
            if hasattr(fs, "write"):
                maybe = fs.write(path, f"completed node {req.node_id}\n")
                if hasattr(maybe, "__await__"):
                    await maybe
            files.append({"path": path, "kind": "created"})
    except Exception as e:
        # Still succeed structural path without FS if unavailable
        logger.warning("fake runtime workspace write skipped: %s", e)
        files.append({"path": "index.html", "kind": "claimed"})

    return NodeExecutionResult(
        success=True,
        task_id=task_id,
        status="succeeded",
        summary=f"fake runtime completed {req.node_id}",
        files_changed=files,
        events_seen=["agent.started", "agent.completed"],
    )


async def inspect_task(task_id: str) -> Optional[dict]:
    """Observe existing task without re-executing — restart recovery helper."""
    if not task_id:
        return None
    try:
        from brain.agent_runtime import get_task
        t = get_task(task_id)
        if t:
            return t.to_dict() if hasattr(t, "to_dict") else {"id": task_id, "status": str(getattr(t, "status", ""))}
    except Exception:
        pass
    try:
        from brain.agent_task_store import load_task
        return await load_task(task_id)
    except Exception:
        return None


async def _run_web_crawl_node(req: NodeExecutionRequest) -> NodeExecutionResult:
    """Mission web_crawl node → durable crawl via Jobs path or direct worker handler.

    Idempotent: if crawl_id already terminal, reuse result. Never silent production fake.
    """
    import asyncio
    from brain.web_mission_exec import materialize_web_crawl_node, apply_crawl_result_to_node
    from execution.web_intel.store import get_crawl
    from execution.web_intel.job_handler import handle_web_crawl_job

    crawl_id = (req.crawl_id or "").strip()
    root = (req.root_url or "").strip()

    # Extract URL from objective if needed
    if not root and not crawl_id:
        import re
        m = re.search(r'https?://[^\s"\']+', req.objective or '')
        if m:
            root = m.group(0)

    if crawl_id:
        existing = get_crawl(crawl_id)
        if existing and (existing.get("status") or "") in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"):
            st = existing["status"]
            ok = st in ("COMPLETED", "PARTIAL")
            return NodeExecutionResult(
                success=ok,
                task_id=crawl_id,
                status="succeeded" if st == "COMPLETED" else ("partial" if st == "PARTIAL" else "failed"),
                summary=f"reused crawl {crawl_id} status={st}",
                error=existing.get("error"),
                raw_terminal={"crawl": existing, "idempotent": True},
            )

    if not crawl_id:
        if not root:
            return NodeExecutionResult(
                success=False, status="error",
                error="web_crawl node missing root_url and crawl_id",
            )
        node = materialize_web_crawl_node(
            user_id=req.user_id,
            goal=req.objective or "web research",
            root_url=root,
            project_id=req.workspace_id,
            mission_id=req.plan_id,
            persona_id=req.persona_id or "research",
            force_refresh=bool(req.force_refresh),
            trace_id=None,
        )
        crawl_id = node["crawl_id"]

    class _Job:
        def __init__(self, cid):
            self.payload = {"crawl_id": cid, "force_refresh": bool(req.force_refresh)}
            self.id = f"mission-web-{cid}"

    try:
        result = await handle_web_crawl_job(_Job(crawl_id))
    except Exception as e:
        return NodeExecutionResult(
            success=False, status="failed", task_id=crawl_id,
            error=f"web_crawl_execution:{type(e).__name__}:{e}",
        )

    crawl = get_crawl(crawl_id) or {}
    st = crawl.get("status") or result.get("status") or "FAILED"
    ok = st in ("COMPLETED", "PARTIAL")
    return NodeExecutionResult(
        success=ok,
        task_id=crawl_id,
        status="succeeded" if st == "COMPLETED" else ("partial" if st == "PARTIAL" else "failed"),
        summary=f"web_crawl {crawl_id} → {st}",
        error=crawl.get("error"),
        raw_terminal={"crawl": crawl, "job_result": result},
        events_seen=["web_crawl.started", f"web_crawl.{st.lower()}"],
    )


def _finalize_node_task(task_req, result: "NodeExecutionResult") -> None:
    """Persist TaskResult + terminal event for a mission node execution."""
    if task_req is None:
        return
    try:
        from core.task_contract import TaskResult, TaskStatus
        from core.task_registry import TaskRegistry
        from core.event_store import EventStore, ProtocolEvent, ProtocolEventType
        if result.status == "cancelled":
            st = TaskStatus.CANCELLED
            et = ProtocolEventType.TASK_CANCELLED
        elif result.success:
            st = TaskStatus.SUCCEEDED
            et = ProtocolEventType.TASK_SUCCEEDED
        else:
            st = TaskStatus.FAILED
            et = ProtocolEventType.TASK_FAILED
        tr = TaskResult(
            task_id=task_req.task_id,
            execution_id=task_req.execution_id,
            worker_slug=task_req.worker_slug,
            status=st,
            output=result.summary,
            errors=[result.error] if result.error else [],
            metadata={"plan_id": task_req.metadata.get("plan_id"), "node_id": task_req.metadata.get("node_id"),
                      "agent_task_id": result.task_id},
            parent_task_id=task_req.parent_task_id,
            root_loop_id=task_req.root_loop_id,
            attempt=task_req.attempt,
        )
        TaskRegistry().save_result(tr)
        EventStore().emit(ProtocolEvent(
            et, task_req.task_id, task_req.execution_id,
            agent_id=f"agent:{task_req.worker_slug}",
            root_task_id=task_req.root_loop_id,
            payload={"status": st.value, "node_status": result.status},
        ))
    except Exception as e:
        logger.debug("durable task finalize failed: %s", e)
