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
    # Persona executable binding (resolved by delegation layer)
    persona_system_prompt: str = ""
    runtime_tools: list[str] = field(default_factory=list)
    agent_id: str = ""


@dataclass
class NodeExecutionResult:
    success: bool
    task_id: Optional[str] = None
    status: str = "unknown"  # succeeded|failed|cancelled|blocked|error
    summary: str = ""
    files_changed: list = field(default_factory=list)
    tools_used: list = field(default_factory=list)
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
            "tools_used": list(self.tools_used or []),
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

    try:
        from brain.agent_runtime import AgentRuntime, AgentContext
        from brain.agent_tools import AgentMode
    except Exception as e:
        return NodeExecutionResult(
            success=False,
            status="error",
            error=f"AGENT_RUNTIME_UNAVAILABLE: {e}",
        )

    # Production path: use configured default provider (OmniRoute-native).
    # Do not rely on BrainLLM falling back silently; make the contract explicit.
    from core.config import settings as _settings
    runtime = AgentRuntime(
        user_id=req.user_id,
        project_id=req.workspace_id or "default",
        tenant_id=None,
        provider=getattr(_settings, "DEFAULT_PROVIDER", None) or "omniroute",
        model=None,  # provider defaults / user prefs resolve inside BrainLLM
        mode=AgentMode.AGENT,
        persona_system_prompt=(req.persona_system_prompt or ""),
        persona_id=req.persona_id or "",
        agent_id=req.agent_id or "",
    )
    context = AgentContext(
        project_id=req.workspace_id or "default",
        user_request=req.objective,
    )
    website_hint = ""
    obj_l = (req.objective or "").lower()
    if any(k in obj_l for k in ("website", "landing", "html", "page", "frontend", "one-page")):
        website_hint = (
            "\n\nBuild the requested website in the assigned DevOS workspace. "
            "Create/edit the actual project files using workspace tools "
            "(create_file / apply_patch / replace_text). "
            "Typical deliverables: index.html, style.css or styles.css, script.js "
            "(or an appropriate Vite/React/Next structure). "
            "Do NOT return the complete website as chat text. "
            "Completion is successful only after the files exist on disk and can be verified."
        )
    objective = (
        f"[orch plan={req.plan_id} node={req.node_id} persona={req.persona_id}]\n"
        f"Workspace: {req.workspace_id}\n"
        f"Authorized caps (canonical): {', '.join(req.effective_caps) or 'none'}\n"
        f"{req.objective}"
        f"{website_hint}"
    )

    result = NodeExecutionResult(success=False, status="running")
    try:
        async for event in runtime.run(objective, context):
            et = (event or {}).get("type") or ""
            data = (event or {}).get("data") or {}
            tid = (event or {}).get("task_id")
            if tid:
                result.task_id = str(tid)
            if et:
                result.events_seen.append(et)
            if et == "agent.completed":
                result.success = data.get("success") is not False
                result.status = "succeeded" if result.success else "failed"
                result.summary = str(data.get("summary") or "")[:2000]
                result.files_changed = list(data.get("files_changed") or [])
                result.raw_terminal = event
            elif et == "agent.cancelled":
                result.success = False
                result.status = "cancelled"
                result.raw_terminal = event
            elif et in ("agent.error", "agent.agent_failed", "agent.agent_blocked"):
                result.success = False
                result.status = "blocked" if "blocked" in et else "failed"
                result.error = str(data.get("message") or data.get("summary") or et)[:500]
                result.raw_terminal = event
    except Exception as e:
        logger.exception("agent runtime node execution failed")
        result.success = False
        result.status = "error"
        err = str(e)[:500]
        if "api key" in err.lower() or "provider" in err.lower() or "model" in err.lower():
            result.error = f"MODEL_UNAVAILABLE: {err}"
        else:
            result.error = f"AGENT_RUNTIME_UNAVAILABLE: {err}" if "AGENT_RUNTIME" not in err else err
    if not result.events_seen and result.status == "running":
        result.success = False
        result.status = "error"
        result.error = result.error or "AGENT_RUNTIME_UNAVAILABLE: no events from runtime"
    return result


async def _fake_runtime(req: NodeExecutionRequest) -> NodeExecutionResult:
    """Test harness: simulates AgentRuntime file tools (create_file), not Nuha LLM write."""
    import uuid
    task_id = f"fake-{uuid.uuid4().hex[:12]}"
    files: list[dict] = []
    tools_used: list[str] = []
    goal = (req.objective or "").lower()
    try:
        from execution.files import FileService
        fs = FileService(req.user_id, req.workspace_id or "default")
        is_site = any(
            k in goal
            for k in ("website", "web site", "landing", "homepage", "shoe", "page for", "site for")
        ) or (req.persona_id or "") == "web"
        if is_site:
            # Simulate Web Agent calling create_file for each artifact
            brand = "Site"
            for token in ("footwalk", "shoe", "carai", "store"):
                if token in goal:
                    brand = token.title()
                    break
            artifacts = {
                "index.html": (
                    f"<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
                    f"<title>{brand}</title>"
                    f"<link rel=\"stylesheet\" href=\"style.css\"></head>"
                    f"<body><h1>{brand}</h1>"
                    f"<p>Built by DevOS Web Agent via create_file.</p>"
                    f"<script src=\"script.js\"></script></body></html>"
                ),
                "style.css": (
                    f"/* {brand} */ body{{font-family:system-ui;margin:2rem;}}"
                    f"h1{{color:#1a1a1a;}}"
                ),
                "script.js": f"console.log('{brand} ready');",
            }
            for path, content in artifacts.items():
                fs.write(path, content)
                files.append({"path": path, "kind": "created", "tool": "create_file"})
                tools_used.append("create_file")
        else:
            path = "orch_result.txt"
            fs.write(path, f"completed node {req.node_id}\n")
            files.append({"path": path, "kind": "created", "tool": "create_file"})
            tools_used.append("create_file")
    except Exception as e:
        logger.warning("fake runtime workspace write skipped: %s", e)
        return NodeExecutionResult(
            success=False,
            task_id=task_id,
            status="failed",
            summary="fake runtime could not write files",
            error=str(e)[:300],
            events_seen=["agent.started", "agent.failed"],
        )

    return NodeExecutionResult(
        success=True,
        task_id=task_id,
        status="succeeded",
        summary=f"web agent create_file x{len(files)}" if tools_used else f"fake runtime {req.node_id}",
        files_changed=files,
        tools_used=tools_used,
        events_seen=["agent.started", "agent.tool_result", "agent.completed"],
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
