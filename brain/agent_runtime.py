"""
DEVOS Agentic IDE — Agent Runtime (tool loop).

LLM → tool call → schema validate → authenticate → resolve tenant/project
  → resolve capability → UCIP / require_authority → execute → Evidence
  → return result to LLM → continue

Does NOT create a parallel permission system. Modes are UX filters only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Callable, Optional

from brain.agent_changes import record_change
from cognitive.hai_control import StrategicController, StrategicDecision
from cognitive.hai_checkpoint import reconcile_with_execution
from brain.agent_tools import (
    AGENT_ACTION_TO_CAP,
    AGENT_TOOL_REGISTRY,
    AgentMode,
    AgentTool,
    SideEffect,
    apply_context_replace,
    apply_unified_diff,
    get_agent_tool,
    list_agent_tools,
    make_line_diff,
    tools_for_prompt,
)
from governance.ucip import ACTION_TO_CAP, AgentIdentity, BudgetPolicy, TrustLevel, UCIPGateway

logger = logging.getLogger("devos.agent_runtime")

# Ensure agent tool capabilities are visible to UCIP ACTION_TO_CAP
for _name, _cap in AGENT_ACTION_TO_CAP.items():
    if _cap and _name not in ACTION_TO_CAP:
        ACTION_TO_CAP[_name] = _cap



def _select_related_tests(changed_files: list, limit: int = 20) -> list[str]:
    """Deterministic bounded test selection from changed paths."""
    out = []
    seen = set()
    for path in changed_files or []:
        p = str(path).replace("\\", "/")
        base = p.rsplit("/", 1)[-1]
        stem = base[:-3] if base.endswith(".py") else base
        candidates = [
            f"tests/test_{stem}.py",
            f"test_{stem}.py",
            f"tests/{stem}_test.py",
            p.replace("/src/", "/tests/").replace(".py", "_test.py"),
        ]
        for c in candidates:
            if c not in seen:
                seen.add(c)
                out.append(c)
            if len(out) >= limit:
                return out
    return out[:limit]


class AgentTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_USER = "waiting_for_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass
class AgentContext:
    workspace: str = "default"
    project_id: str = "default"
    active_file: Optional[str] = None
    selected_text: Optional[str] = None
    cursor_position: Optional[dict] = None
    open_files: list[str] = field(default_factory=list)
    recent_files: list[str] = field(default_factory=list)
    git_status_summary: Optional[str] = None
    diagnostics: list[dict] = field(default_factory=list)
    terminal_context: Optional[str] = None
    user_request: str = ""

    def to_prompt_block(self) -> str:
        parts = [
            f"project: {self.project_id}",
            f"workspace: {self.workspace}",
        ]
        if self.active_file:
            parts.append(f"active_file: {self.active_file}")
        if self.selected_text:
            sel = self.selected_text[:2000]
            parts.append(f"selected_text:\n```\n{sel}\n```")
        if self.open_files:
            parts.append("open_files: " + ", ".join(self.open_files[:20]))
        if self.recent_files:
            parts.append("recent_files: " + ", ".join(self.recent_files[:10]))
        if self.git_status_summary:
            parts.append(f"git_status:\n{self.git_status_summary[:1500]}")
        if self.diagnostics:
            diag_lines = []
            for d in self.diagnostics[:15]:
                diag_lines.append(
                    f"  {d.get('file', '?')}:{d.get('line', '?')} {d.get('message', '')}"
                )
            parts.append("diagnostics:\n" + "\n".join(diag_lines))
        if self.terminal_context:
            parts.append(f"terminal_context:\n{self.terminal_context[:1500]}")
        return "\n".join(parts)


@dataclass
class AgentTask:
    id: str
    user_id: str
    tenant_id: Optional[str]
    project_id: str
    session_id: str
    objective: str
    mode: AgentMode
    status: AgentTaskStatus = AgentTaskStatus.QUEUED
    current_tool: Optional[str] = None
    files_changed: list[dict] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    error: Optional[str] = None
    summary: Optional[str] = None
    cancel_requested: bool = False
    hai_state: Optional[dict] = None
    lifecycle: str = "created"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "objective": self.objective,
            "mode": self.mode.value,
            "status": self.status.value,
            "current_tool": self.current_tool,
            "files_changed": self.files_changed,
            "tools_used": self.tools_used,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "correlation_id": self.correlation_id,
            "error": self.error,
            "summary": self.summary,
            "hai_state": self.hai_state,
            "lifecycle": getattr(self, "lifecycle", "created"),
        }


# In-process task store (hot path). Durable mirror is AgentTaskRecord via agent_task_store.
# ExecutionJob remains the durable work unit for scripts/workflows — do not conflate.
_TASKS: dict[str, AgentTask] = {}
_TASK_EVENTS: dict[str, list[dict]] = {}
_CANCEL_FLAGS: dict[str, asyncio.Event] = {}
_EVENT_SEQ: dict[str, int] = {}


def get_task(task_id: str) -> Optional[AgentTask]:
    return _TASKS.get(task_id)


def get_task_events(task_id: str, after_seq: int = 0) -> list[dict]:
    events = _TASK_EVENTS.get(task_id) or []
    if after_seq <= 0:
        return list(events)
    return [e for e in events if int(e.get("seq") or 0) > after_seq]


def list_tasks_for_user(user_id: str, limit: int = 20) -> list[dict]:
    items = [t for t in _TASKS.values() if t.user_id == user_id]
    items.sort(key=lambda t: t.started_at or "", reverse=True)
    return [t.to_dict() for t in items[:limit]]


def request_cancel(task_id: str) -> bool:
    task = _TASKS.get(task_id)
    if not task:
        return False
    task.cancel_requested = True
    ev = _CANCEL_FLAGS.get(task_id)
    if ev:
        ev.set()
    return True


def _emit(task: AgentTask, event_type: str, data: Optional[dict] = None) -> dict:
    seq = _EVENT_SEQ.get(task.id, 0) + 1
    _EVENT_SEQ[task.id] = seq
    evt = {
        "type": event_type,
        "task_id": task.id,
        "correlation_id": task.correlation_id,
        "project_id": task.project_id,
        "seq": seq,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }
    _TASK_EVENTS.setdefault(task.id, []).append(evt)
    if len(_TASK_EVENTS[task.id]) > 500:
        _TASK_EVENTS[task.id] = _TASK_EVENTS[task.id][-500:]
    # Best-effort durable mirror (async scheduled by caller when possible)
    try:
        loop = asyncio.get_running_loop()
        from brain.agent_task_store import append_event, persist_task
        loop.create_task(append_event(task.id, evt))
        # Persist status snapshots on lifecycle events
        if event_type in (
            "agent.started", "agent.completed", "agent.cancelled",
            "agent.error", "agent.file_changed",
        ):
            loop.create_task(persist_task(task))
    except RuntimeError:
        pass
    return evt


SYSTEM_PROMPT = """You are Wozzy/Nuha, the DEVOS coding agent inside an IDE.
You operate under UCIP governance. You may ONLY use the tools listed below.
Never invent tool names. Prefer progressive exploration:
  list/search → read relevant excerpts → patch small changes → run tests → iterate.

When the task is complete, respond with a final message that does NOT include a tool call JSON.
To call a tool, output EXACTLY one JSON object on its own (no markdown fences), shape:
{"thought":"brief safe status","action":"tool_name","action_input":{...}}

Rules:
- Prefer apply_patch / replace_text over rewriting entire files.
- Do not request destructive git operations (reset --hard, force push, clean -fd).
- Treat secrets and .env as off-limits unless the user explicitly authorizes.
- Stop when the objective is met or you are blocked; summarize clearly.
- "thought" is safe progress text for the user — not private chain-of-thought.

Available tools:
{tools}
"""


def _parse_tool_call(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if not text:
        return None
    # Strip optional fences
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # Find first JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "action" not in obj:
        return None
    return obj


class AgentRuntime:
    """Governed agent tool loop for the IDE."""

    MAX_STEPS = 24

    def __init__(
        self,
        user_id: str,
        project_id: str,
        tenant_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        mode: AgentMode = AgentMode.AGENT,
        trust_level: TrustLevel = TrustLevel.OPERATOR,
        capabilities: Optional[set[str]] = None,
    ):
        self.user_id = user_id
        self.project_id = project_id
        self.tenant_id = tenant_id
        self.provider = provider
        self.model = model
        self.mode = mode

        # Default capability set for IDE agent: filesystem + vcs write + shell.
        # UCIP still evaluates each call; HITL escalations still apply.
        default_caps = {
            "ucip:filesystem.read",
            "ucip:filesystem.write",
            "ucip:filesystem.delete",
            "ucip:vcs.write",
            "ucip:execution.shell",
            "ucip:execution.python",
            "ucip:execution.bash",
            "ucip:memory.read",
            "ucip:memory.write",
            "ucip:search.web",
        }
        caps = capabilities if capabilities is not None else default_caps

        session_id = str(uuid.uuid4())
        agent_id = hashlib.sha256(
            f"{user_id}:{session_id}:ide-agent".encode()
        ).hexdigest()[:32]

        self.agent = AgentIdentity(
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            trust_level=trust_level,
            capabilities=caps,
            metadata={"role": "ide_agent", "project_id": project_id},
        )
        self.gateway = UCIPGateway(self.agent, BudgetPolicy(
            max_iterations=24,
            max_execution_calls=40,
            max_total_runtime_s=600,
            max_tokens_per_task=200_000,
        ))
        self.session_id = session_id

    async def run(
        self,
        objective: str,
        context: Optional[AgentContext] = None,
    ) -> AsyncIterator[dict]:
        ctx = context or AgentContext(project_id=self.project_id, user_request=objective)
        ctx.user_request = objective
        ctx.project_id = self.project_id

        task = AgentTask(
            id=str(uuid.uuid4()),
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            session_id=self.session_id,
            objective=objective,
            mode=self.mode,
            status=AgentTaskStatus.RUNNING,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        _TASKS[task.id] = task
        _CANCEL_FLAGS[task.id] = asyncio.Event()
        try:
            from brain.agent_task_store import persist_task
            await persist_task(task)
        except Exception:
            logger.debug("initial task persist failed", exc_info=True)

        yield _emit(task, "agent.started", {
            "objective": objective,
            "mode": self.mode.value,
            "task": task.to_dict(),
        })

        # Stage 3J — HAI control plane (Agent mode only; no second tool loop)
        hai = None
        if self.mode == AgentMode.AGENT:
            hai = StrategicController()
            splan = hai.start(objective)
            task.lifecycle = hai.control.lifecycle
            task.hai_state = hai.control.to_state_dict()
            yield _emit(task, "agent.strategic_plan_created", splan)
            yield _emit(task, "agent.strategic_subgoal_selected", {
                "subgoal_id": splan.get("selected_subgoal"),
                "decision": splan.get("decision"),
                "reason_code": splan.get("reason_code"),
            })
            # Persist checkpoint at plan boundary
            try:
                from brain.agent_task_store import persist_hai_checkpoint
                cp = hai.control.checkpoint(task.id, correlation_id=task.correlation_id, state_version=1)
                await persist_hai_checkpoint(task.id, cp.to_dict())
                yield _emit(task, "agent.hai_checkpoint_created", {
                    "state_version": 1,
                    "lifecycle": hai.control.lifecycle,
                    "checksum": cp.checksum,
                })
            except Exception:
                logger.debug("hai checkpoint persist failed", exc_info=True)
            if splan.get("decision") == StrategicDecision.DELEGATE_WORKFLOW.value:
                yield _emit(task, "agent.workflow_delegated", splan)
            if splan.get("decision") == StrategicDecision.DELEGATE_COORDINATOR.value:
                yield _emit(task, "agent.coordinator_delegated", splan)
            if splan.get("decision") in (
                StrategicDecision.BLOCK.value, StrategicDecision.FAIL.value,
            ):
                task.status = AgentTaskStatus.BLOCKED if splan["decision"] == StrategicDecision.BLOCK.value else AgentTaskStatus.FAILED
                task.summary = splan.get("message") or splan.get("reason_code")
                task.completed_at = datetime.now(timezone.utc).isoformat()
                yield _emit(task, "agent.agent_blocked" if task.status == AgentTaskStatus.BLOCKED else "agent.agent_failed", splan)
                yield _emit(task, "agent.completed", {"summary": task.summary, "hai": splan})
                return

        tools_block = tools_for_prompt(self.mode)
        system = SYSTEM_PROMPT.format(tools=tools_block)
        messages: list[dict] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"CONTEXT:\n{ctx.to_prompt_block()}\n\n"
                    f"OBJECTIVE:\n{objective}"
                ),
            },
        ]

        from brain.llm import BrainLLM
        brain = BrainLLM(
            provider=self.provider,
            model=self.model,
            user_id=self.user_id,
            purpose="coding",
        )

        try:
            for step in range(self.MAX_STEPS):
                if task.cancel_requested or _CANCEL_FLAGS[task.id].is_set():
                    task.status = AgentTaskStatus.CANCELLED
                    task.completed_at = datetime.now(timezone.utc).isoformat()
                    yield _emit(task, "agent.cancelled", {"reason": "user_cancelled"})
                    return

                yield _emit(task, "agent.thinking", {
                    "step": step + 1,
                    "message": f"Planning next action (step {step + 1})",
                })

                try:
                    text = await brain.stream_chat(messages)
                except Exception as e:
                    task.status = AgentTaskStatus.FAILED
                    task.error = str(e)
                    task.completed_at = datetime.now(timezone.utc).isoformat()
                    yield _emit(task, "agent.error", {"message": str(e)})
                    return

                if text.startswith("All providers failed"):
                    task.status = AgentTaskStatus.FAILED
                    task.error = text
                    task.completed_at = datetime.now(timezone.utc).isoformat()
                    yield _emit(task, "agent.error", {"message": text})
                    return

                call = _parse_tool_call(text)
                if not call:
                    # Natural-language final answer — HAI may veto premature success
                    if hai is not None:
                        gate = hai.evaluate_natural_language_completion(text)
                        task.hai_state = hai.control.to_state_dict()
                        task.lifecycle = hai.control.lifecycle
                        if gate.get("decision") == StrategicDecision.VERIFY.value:
                            yield _emit(task, "agent.verification_started", gate)
                            messages.append({"role": "assistant", "content": text})
                            messages.append({
                                "role": "user",
                                "content": (
                                    "HAI: verification is required before completion. "
                                    f"Reason: {gate.get('reason_code')}. "
                                    "Use an appropriate verification tool (e.g. run_tests, "
                                    "get_diagnostics) and report evidence. Do not claim done yet."
                                ),
                            })
                            continue
                        if gate.get("decision") == StrategicDecision.CONTINUE.value:
                            messages.append({"role": "assistant", "content": text})
                            messages.append({
                                "role": "user",
                                "content": (
                                    "HAI: completion criteria not satisfied "
                                    f"({gate.get('reason_code')}). Continue working on "
                                    f"subgoal={gate.get('selected_subgoal')}."
                                ),
                            })
                            continue
                        if gate.get("decision") in (
                            StrategicDecision.BLOCK.value, StrategicDecision.FAIL.value,
                        ):
                            task.status = (
                                AgentTaskStatus.BLOCKED
                                if gate["decision"] == StrategicDecision.BLOCK.value
                                else AgentTaskStatus.FAILED
                            )
                            task.summary = gate.get("message") or gate.get("reason_code")
                            task.completed_at = datetime.now(timezone.utc).isoformat()
                            yield _emit(task, "agent.agent_blocked", gate)
                            yield _emit(task, "agent.completed", {
                                "summary": task.summary, "success": False, "hai": gate,
                            })
                            return
                        if gate.get("decision") != StrategicDecision.COMPLETE.value:
                            messages.append({"role": "assistant", "content": text})
                            messages.append({
                                "role": "user",
                                "content": f"HAI decision={gate.get('decision')}: continue.",
                            })
                            continue
                        # COMPLETE — durable terminal checkpoint required
                        try:
                            from brain.agent_task_store import persist_hai_checkpoint
                            cp = hai.control.checkpoint(
                                task.id, correlation_id=task.correlation_id,
                                state_version=hai.control.iteration_count,
                            )
                            ok_cp = await persist_hai_checkpoint(task.id, cp.to_dict())
                            if not ok_cp:
                                task.status = AgentTaskStatus.BLOCKED
                                task.summary = "Terminal HAI checkpoint failed; refusing success"
                                task.error = "checkpoint_persist_failed"
                                task.completed_at = datetime.now(timezone.utc).isoformat()
                                yield _emit(task, "agent.agent_blocked", {
                                    "reason_code": "checkpoint_persist_failed",
                                })
                                yield _emit(task, "agent.completed", {
                                    "summary": task.summary, "success": False,
                                })
                                return
                        except Exception as e:
                            task.status = AgentTaskStatus.BLOCKED
                            task.summary = f"Terminal checkpoint error: {e}"
                            task.error = "checkpoint_persist_failed"
                            task.completed_at = datetime.now(timezone.utc).isoformat()
                            yield _emit(task, "agent.completed", {
                                "summary": task.summary, "success": False,
                            })
                            return
                        task.status = AgentTaskStatus.SUCCEEDED
                        task.summary = (text or gate.get("message") or "Complete")[:4000]
                        task.completed_at = datetime.now(timezone.utc).isoformat()
                        yield _emit(task, "agent.agent_completed", {"summary": task.summary})
                        yield _emit(task, "agent.completed", {
                            "summary": task.summary,
                            "files_changed": task.files_changed,
                            "tools_used": task.tools_used,
                            "success": True,
                            "hai": gate,
                        })
                        return
                    # Non-Agent modes: legacy natural-language completion
                    task.status = AgentTaskStatus.SUCCEEDED
                    task.summary = text[:4000]
                    task.completed_at = datetime.now(timezone.utc).isoformat()
                    yield _emit(task, "agent.completed", {
                        "summary": task.summary,
                        "files_changed": task.files_changed,
                        "tools_used": task.tools_used,
                    })
                    return

                action = str(call.get("action") or "").strip()
                action_input = call.get("action_input", {})
                thought = str(call.get("thought") or "")[:500]

                if thought:
                    yield _emit(task, "agent.thinking", {"message": thought, "step": step + 1})

                # Mode filter (UX only)
                allowed = list_agent_tools(self.mode)
                allowed_names = {t["name"] for t in allowed}
                if action not in allowed_names:
                    messages.append({"role": "assistant", "content": text})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Tool '{action}' is not available in mode '{self.mode.value}'. "
                            f"Allowed: {sorted(allowed_names)}. Choose another tool or finish."
                        ),
                    })
                    yield _emit(task, "agent.tool_result", {
                        "tool": action,
                        "ok": False,
                        "error": f"tool not allowed in mode {self.mode.value}",
                    })
                    continue

                tool = get_agent_tool(action)
                if not tool:
                    messages.append({"role": "assistant", "content": text})
                    messages.append({
                        "role": "user",
                        "content": f"Unknown tool '{action}'. Use only registered tools.",
                    })
                    yield _emit(task, "agent.tool_result", {
                        "tool": action, "ok": False, "error": "unknown tool",
                    })
                    continue

                if not isinstance(action_input, dict):
                    # tolerate string payloads
                    if isinstance(action_input, str):
                        try:
                            action_input = json.loads(action_input)
                        except Exception:
                            action_input = {"input": action_input}
                    else:
                        action_input = {}

                valid, verr = tool.validate_args(action_input)
                if not valid:
                    messages.append({"role": "assistant", "content": text})
                    messages.append({
                        "role": "user",
                        "content": f"Invalid arguments for {action}: {verr}",
                    })
                    yield _emit(task, "agent.tool_result", {
                        "tool": action, "ok": False, "error": verr,
                    })
                    continue

                # UCIP gate
                input_str = json.dumps(action_input, sort_keys=True)[:8000]
                decision = self.gateway.request(
                    action,
                    input_str,
                    context={
                        "project_id": self.project_id,
                        "tenant_id": self.tenant_id,
                        "correlation_id": task.correlation_id,
                        "source": "ide_agent",
                    },
                )

                if getattr(decision, "decision", None) == "DENY" or (
                    hasattr(decision, "approved") and not decision.approved()
                    and not (hasattr(decision, "needs_human") and decision.needs_human())
                ):
                    reason = getattr(decision, "reason", None) or "UCIP denied"
                    # Some PolicyDecision shapes use enum
                    dec_val = getattr(decision, "decision", None)
                    if hasattr(dec_val, "value"):
                        dec_val = dec_val.value
                    if dec_val in ("DENY", "deny") or (
                        hasattr(decision, "approved") and not decision.approved()
                        and not (hasattr(decision, "needs_human") and decision.needs_human())
                    ):
                        messages.append({"role": "assistant", "content": text})
                        messages.append({
                            "role": "user",
                            "content": f"[UCIP DENIED] {reason}. Choose a permitted action.",
                        })
                        yield _emit(task, "agent.tool_result", {
                            "tool": action, "ok": False, "error": f"UCIP DENIED: {reason}",
                            "side_effect": tool.side_effect.value,
                        })
                        continue

                if hasattr(decision, "needs_human") and decision.needs_human():
                    task.status = AgentTaskStatus.WAITING_FOR_USER
                    yield _emit(task, "agent.tool_result", {
                        "tool": action,
                        "ok": False,
                        "error": "requires_authority",
                        "reason": getattr(decision, "reason", "HITL required"),
                        "side_effect": tool.side_effect.value,
                    })
                    messages.append({"role": "assistant", "content": text})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[REQUIRES AUTHORITY] {getattr(decision, 'reason', '')}. "
                            "Do not retry this tool; inform the user."
                        ),
                    })
                    task.status = AgentTaskStatus.RUNNING
                    continue

                task.status = AgentTaskStatus.WAITING_FOR_TOOL
                task.current_tool = action
                yield _emit(task, "agent.tool_call", {
                    "tool": action,
                    "arguments": action_input,
                    "side_effect": tool.side_effect.value,
                    "risk": tool.risk.value,
                    "capability": tool.capability,
                })

                if task.cancel_requested:
                    task.status = AgentTaskStatus.CANCELLED
                    task.completed_at = datetime.now(timezone.utc).isoformat()
                    yield _emit(task, "agent.cancelled", {"reason": "user_cancelled"})
                    return

                # Stage 3M — operation ledger for consequential tools
                op_id = None
                from brain.agent_tools import SideEffect
                from governance.execution_operations import (
                    is_consequential_side_effect, reserve_operation, mark_running,
                    complete_operation, digest_payload,
                )
                se = getattr(tool.side_effect, "value", str(tool.side_effect))
                if is_consequential_side_effect(se):
                    op_id = await reserve_operation(
                        owner_id=self.user_id,
                        tenant_id=self.tenant_id,
                        actor_id=self.user_id,
                        task_id=task.id,
                        operation_type="agent_tool",
                        tool_name=action,
                        correlation_id=task.correlation_id,
                        args=action_input if isinstance(action_input, dict) else {},
                    )
                    if not op_id:
                        result = {"ok": False, "error": "operation_reservation_failed", "execute": False, "retry": False}
                        task.tools_used.append(action)
                        yield _emit(task, "agent.tool_result", {
                            "tool": action, "ok": False, "error": "operation_reservation_failed",
                        })
                        messages.append({"role": "assistant", "content": text})
                        messages.append({
                            "role": "user",
                            "content": "OPERATION RESERVE FAILED. Do not assume the side effect ran.",
                        })
                        continue
                    if not await mark_running(op_id):
                        result = {
                            "ok": False,
                            "error": "operation_start_failed",
                            "operation_id": op_id,
                            "execute": False,
                            "retry": False,
                        }
                        task.tools_used.append(action)
                        yield _emit(task, "agent.tool_result", {
                            "tool": action, "ok": False, "error": "operation_start_failed",
                            "operation_id": op_id,
                        })
                        messages.append({"role": "assistant", "content": text})
                        messages.append({
                            "role": "user",
                            "content": "OPERATION START FAILED. Do not assume the side effect ran.",
                        })
                        continue
                    # expose to HAI checkpoint binding
                    if hasattr(task, "last_operation_id"):
                        task.last_operation_id = op_id
                    else:
                        setattr(task, "last_operation_id", op_id)
                    try:
                        from governance.failure_injection import maybe_crash
                        maybe_crash("after_operation_running")
                    except Exception:
                        pass

                result = await self._execute_tool(task, tool, action_input)

                if op_id:
                    try:
                        from governance.failure_injection import maybe_crash
                        maybe_crash("after_side_effect_before_evidence")
                    except Exception:
                        pass
                    # Evidence already recorded below; complete after evidence when possible
                    result = dict(result or {})
                    result["operation_id"] = op_id

                task.tools_used.append(action)
                task.current_tool = None
                task.status = AgentTaskStatus.RUNNING

                # Record evidence for consequential tools
                await self._record_evidence(task, tool, action_input, result)

                # Stage 3M — complete operation after evidence attempt
                if op_id:
                    try:
                        from governance.failure_injection import maybe_crash
                        maybe_crash("after_evidence_before_operation_complete")
                    except Exception:
                        pass
                    await complete_operation(
                        op_id,
                        success=bool(result.get("ok")),
                        evidence_id=(result.get("evidence_id") if isinstance(result, dict) else None),
                        result_digest=digest_payload(result) if isinstance(result, dict) else None,
                        error=None if result.get("ok") else str(result.get("error") or "failed")[:500],
                    )
                    try:
                        from governance.failure_injection import maybe_crash
                        maybe_crash("after_operation_complete_before_hai_checkpoint")
                    except Exception:
                        pass

                yield _emit(task, "agent.tool_result", {
                    "tool": action,
                    "ok": result.get("ok", False),
                    "result": _truncate_result(result),
                    "side_effect": tool.side_effect.value,
                })

                if hai is not None:
                    hout = hai.on_tool_result(
                        action,
                        bool(result.get("ok")),
                        summary=str(result.get("error") or result.get("message") or "")[:300],
                        arguments=action_input if isinstance(action_input, dict) else {},
                        result=result if isinstance(result, dict) else {},
                    )
                    task.hai_state = hai.control.to_state_dict()
                    task.lifecycle = hai.control.lifecycle
                    yield _emit(task, "agent.tactical_action_selected" if hout.get("decision") == "continue" else "agent.strategic_subgoal_selected", hout)
                    if hout.get("decision") == StrategicDecision.VERIFY.value:
                        yield _emit(task, "agent.verification_started", hout)
                    if hout.get("decision") == StrategicDecision.BLOCK.value:
                        task.status = AgentTaskStatus.BLOCKED
                        task.summary = hout.get("message") or "blocked"
                        task.completed_at = datetime.now(timezone.utc).isoformat()
                        yield _emit(task, "agent.loop_detected", hout)
                        yield _emit(task, "agent.agent_blocked", hout)
                        yield _emit(task, "agent.completed", {"summary": task.summary, "hai": hout})
                        return
                    if hout.get("decision") == StrategicDecision.COMPLETE.value:
                        try:
                            from brain.agent_task_store import persist_hai_checkpoint
                            cp = hai.control.checkpoint(
                                task.id, correlation_id=task.correlation_id,
                                state_version=hai.control.plan_version,
                            )
                            ok_cp = await persist_hai_checkpoint(task.id, cp.to_dict())
                            if not ok_cp:
                                task.status = AgentTaskStatus.BLOCKED
                                task.error = "checkpoint_persist_failed"
                                task.summary = "Terminal checkpoint failed; refusing success"
                                task.completed_at = datetime.now(timezone.utc).isoformat()
                                yield _emit(task, "agent.completed", {
                                    "summary": task.summary, "success": False, "hai": hout,
                                })
                                return
                        except Exception as e:
                            task.status = AgentTaskStatus.BLOCKED
                            task.error = "checkpoint_persist_failed"
                            task.summary = f"Terminal checkpoint error: {e}"
                            task.completed_at = datetime.now(timezone.utc).isoformat()
                            yield _emit(task, "agent.completed", {
                                "summary": task.summary, "success": False,
                            })
                            return
                        task.status = AgentTaskStatus.SUCCEEDED
                        task.summary = hout.get("message") or "Objective complete"
                        task.completed_at = datetime.now(timezone.utc).isoformat()
                        yield _emit(task, "agent.agent_completed", {"summary": task.summary})
                        yield _emit(task, "agent.completed", {
                            "summary": task.summary,
                            "files_changed": task.files_changed,
                            "tools_used": task.tools_used,
                            "success": True,
                            "hai": hout,
                        })
                        return
                    if hout.get("decision") == StrategicDecision.REPLAN.value:
                        yield _emit(task, "agent.replan_started", hout)
                        plan_ctx = hai.consume_plan_update()
                        if plan_ctx:
                            messages.append({
                                "role": "user",
                                "content": (
                                    "HAI REPLAN active. Use the updated plan; do not continue the old plan.\n"
                                    + json.dumps(plan_ctx)[:3000]
                                ),
                            })
                    # Checkpoint after consequential tools
                    if action in (
                        "apply_patch", "replace_text", "create_file", "write_file",
                        "rename_file", "delete_file", "run_tests", "run_command",
                    ):
                        try:
                            from brain.agent_task_store import persist_hai_checkpoint
                            cp = hai.control.checkpoint(
                                task.id, correlation_id=task.correlation_id,
                                state_version=hai.control.iteration_count,
                            )
                            ok_cp = await persist_hai_checkpoint(task.id, cp.to_dict())
                            if ok_cp:
                                yield _emit(task, "agent.hai_checkpoint_created", {
                                    "state_version": hai.control.iteration_count,
                                    "lifecycle": hai.control.lifecycle,
                                })
                            else:
                                # Consequential boundary: do not silently continue
                                yield _emit(task, "agent.agent_blocked", {
                                    "reason_code": "checkpoint_persist_failed",
                                    "message": "Durable HAI checkpoint failed after consequential action",
                                })
                                task.status = AgentTaskStatus.BLOCKED
                                task.error = "checkpoint_persist_failed"
                                task.summary = "Checkpoint persistence failed after consequential action"
                                task.completed_at = datetime.now(timezone.utc).isoformat()
                                yield _emit(task, "agent.completed", {
                                    "summary": task.summary, "success": False,
                                })
                                return
                        except Exception as e:
                            logger.exception("post-tool checkpoint failed")
                            task.status = AgentTaskStatus.BLOCKED
                            task.error = "checkpoint_persist_failed"
                            task.summary = f"Checkpoint error: {e}"
                            task.completed_at = datetime.now(timezone.utc).isoformat()
                            yield _emit(task, "agent.completed", {
                                "summary": task.summary, "success": False,
                            })
                            return

                if result.get("file_changed"):
                    fc = result["file_changed"]
                    task.files_changed.append(fc)
                    yield _emit(task, "agent.file_changed", fc)

                messages.append({"role": "assistant", "content": text})
                messages.append({
                    "role": "user",
                    "content": (
                        f"TOOL RESULT [{action}]:\n"
                        + json.dumps(_truncate_result(result), indent=2)[:6000]
                    ),
                })

            # Budget exhaustion is NOT successful completion (Stage 3J)
            task.status = AgentTaskStatus.BLOCKED
            task.summary = (
                "Agent execution budget exhausted (MAX_STEPS). "
                "Partial progress may exist; completion criteria not satisfied."
            )
            task.error = "max_steps_reached"
            task.completed_at = datetime.now(timezone.utc).isoformat()
            if hai is not None:
                hai.control.lifecycle = "blocked"
                task.lifecycle = "blocked"
                task.hai_state = hai.control.to_state_dict()
            yield _emit(task, "agent.agent_blocked", {
                "reason_code": "max_steps_reached",
                "summary": task.summary,
            })
            yield _emit(task, "agent.completed", {
                "summary": task.summary,
                "files_changed": task.files_changed,
                "tools_used": task.tools_used,
                "max_steps_reached": True,
                "success": False,
            })
        except Exception as e:
            logger.exception("agent runtime failure")
            task.status = AgentTaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now(timezone.utc).isoformat()
            yield _emit(task, "agent.error", {"message": str(e)})

    async def _execute_tool(
        self, task: AgentTask, tool: AgentTool, args: dict
    ) -> dict:
        name = tool.name
        self._current_task = task
        try:
            if name == "list_files":
                return await self._list_files(args)
            if name == "read_file":
                return await self._read_file(args)
            if name == "search_files":
                return await self._search_files(args)
            if name == "get_file_metadata":
                return await self._get_file_metadata(args)
            if name == "create_file":
                return await self._create_file(args)
            if name == "apply_patch":
                return await self._apply_patch(args)
            if name == "replace_text":
                return await self._replace_text(args)
            if name == "rename_file":
                return await self._rename_file(args)
            if name == "delete_file":
                return await self._delete_file(args)
            if name in ("run_command", "run_tests", "run_build", "run_linter"):
                return await self._run_command(task, name, args)
            if name.startswith("git_"):
                return await self._git(name, args)
            if name in ("get_job", "get_job_logs", "get_evidence"):
                return await self._diagnostics(name, args)
            if name in ("list_workflows", "inspect_workflow", "execute_workflow"):
                return await self._workflows(name, args)
            if name in (
                "get_project_metadata", "get_test_files", "get_build_system",
                "get_package_dependencies", "find_symbol",
            ):
                return await self._repo_intel(name, args)
            if name == "select_related_tests":
                files = args.get("changed_files") or []
                limit = int(args.get("limit") or 20)
                return {"ok": True, "tests": _select_related_tests(files, limit)}
            return {"ok": False, "error": f"handler not implemented: {name}"}
        except Exception as e:
            logger.exception("tool %s failed", name)
            return {"ok": False, "error": str(e)}

    def _fs(self):
        from execution.files import FileService
        return FileService(self.user_id, self.project_id)

    def _git_svc(self):
        from execution.vcs import GitService
        return GitService(self.user_id, self.project_id)


    def _safe_read(self, path: str) -> Optional[str]:
        try:
            return self._fs().read(path).get("content")
        except Exception:
            return None

    def _note_change(self, task: AgentTask, path: str, kind: str, before, after, meta=None):
        try:
            rec = record_change(
                task_id=task.id,
                user_id=self.user_id,
                project_id=self.project_id,
                path=path,
                change_kind=kind,
                before_content=before,
                after_content=after,
                meta=meta or {},
            )
            return rec.id
        except Exception:
            logger.debug("change snapshot failed", exc_info=True)
            return None

    async def _list_files(self, args: dict) -> dict:
        fs = self._fs()
        items = fs.tree()
        prefix = (args.get("path") or "").strip().strip("/")
        max_entries = int(args.get("max_entries") or 200)
        if prefix:
            items = [i for i in items if i["path"] == prefix or i["path"].startswith(prefix + "/")]
        items = items[:max_entries]
        return {"ok": True, "files": items, "count": len(items)}

    async def _read_file(self, args: dict) -> dict:
        fs = self._fs()
        path = args["path"]
        data = fs.read(path)
        content = data.get("content") or ""
        start = args.get("start_line")
        end = args.get("end_line")
        if start is not None or end is not None:
            lines = content.splitlines()
            s = max(1, int(start or 1)) - 1
            e = int(end or len(lines))
            content = "\n".join(lines[s:e])
            data["start_line"] = s + 1
            data["end_line"] = e
        # Bound content returned to model
        if len(content) > 80_000:
            content = content[:80_000] + "\n...[truncated]..."
            data["truncated"] = True
        data["content"] = content
        data["ok"] = True
        return data

    async def _search_files(self, args: dict) -> dict:
        fs = self._fs()
        query = (args.get("query") or "").strip()
        if not query:
            return {"ok": True, "files": []}
        max_results = int(args.get("max_results") or 20)
        glob = (args.get("glob") or "").strip()
        tree = fs.tree()
        q = query.lower()
        results = []
        for item in tree:
            if item["type"] != "file":
                continue
            if glob:
                import fnmatch
                if not fnmatch.fnmatch(item["path"], glob) and not fnmatch.fnmatch(
                    item["path"].split("/")[-1], glob
                ):
                    continue
            score = 0
            snippet = None
            if q in item["path"].lower():
                score += 20
            if not item.get("is_binary"):
                try:
                    content = fs.read(item["path"])["content"] or ""
                except Exception:
                    content = ""
                idx = content.lower().find(q)
                if idx >= 0:
                    score += 10
                    line_start = content.rfind("\n", 0, idx) + 1
                    line_end = content.find("\n", idx)
                    if line_end < 0:
                        line_end = len(content)
                    snippet = content[line_start:line_end].strip()[:200]
            if score > 0:
                results.append({
                    "path": item["path"],
                    "score": score,
                    "snippet": snippet,
                })
        results.sort(key=lambda r: r["score"], reverse=True)
        return {"ok": True, "files": results[:max_results]}

    async def _get_file_metadata(self, args: dict) -> dict:
        fs = self._fs()
        path = args["path"]
        p = fs._resolve(path)
        if not p.exists():
            return {"ok": False, "error": "not found"}
        st = p.stat()
        return {
            "ok": True,
            "path": path,
            "is_dir": p.is_dir(),
            "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        }

    async def _create_file(self, args: dict) -> dict:
        fs = self._fs()
        path = args["path"]
        content = args.get("content") or ""
        before = self._safe_read(path)
        try:
            fs.create(path, is_dir=False)
        except FileExistsError:
            return {"ok": False, "error": "file already exists"}
        except Exception as e:
            if "exist" in str(e).lower():
                return {"ok": False, "error": "file already exists"}
            raise
        if content:
            fs.write(path, content)
        cid = None
        # task passed via self._current_task if set
        task = getattr(self, "_current_task", None)
        if task:
            cid = self._note_change(task, path, "created", before, content)
        return {
            "ok": True,
            "path": path,
            "change_id": cid,
            "file_changed": {
                "path": path,
                "change": "created",
                "change_id": cid,
                "additions": content.count("\n") + (1 if content else 0),
                "deletions": 0,
            },
        }


    async def _apply_patch(self, args: dict) -> dict:
        fs = self._fs()
        path = args["path"]
        try:
            current = fs.read(path)["content"] or ""
        except FileNotFoundError:
            return {"ok": False, "error": "file not found"}

        if args.get("expected_hash"):
            h = hashlib.sha256(current.encode("utf-8")).hexdigest()
            if h != args["expected_hash"]:
                return {"ok": False, "error": "patch_conflict: expected_hash mismatch"}

        if args.get("unified_diff"):
            ok, new_content, err = apply_unified_diff(current, args["unified_diff"])
        elif args.get("old_text") is not None:
            ok, new_content, err = apply_context_replace(
                current, args.get("old_text") or "", args.get("new_text") or ""
            )
        else:
            return {"ok": False, "error": "provide unified_diff or old_text/new_text"}

        if not ok:
            return {"ok": False, "error": err}

        fs.write(path, new_content)
        diff = make_line_diff(current, new_content)
        additions = sum(1 for d in diff if d["type"] == "add")
        deletions = sum(1 for d in diff if d["type"] == "del")
        task = getattr(self, "_current_task", None)
        cid = self._note_change(task, path, "patched", current, new_content) if task else None
        return {
            "ok": True,
            "path": path,
            "additions": additions,
            "deletions": deletions,
            "diff": diff[:200],
            "change_id": cid,
            "file_changed": {
                "path": path,
                "change": "patched",
                "change_id": cid,
                "additions": additions,
                "deletions": deletions,
                "diff": diff[:100],
            },
        }


    async def _replace_text(self, args: dict) -> dict:
        fs = self._fs()
        path = args["path"]
        try:
            current = fs.read(path)["content"] or ""
        except FileNotFoundError:
            return {"ok": False, "error": "file not found"}
        old, new = args["old_text"], args["new_text"]
        if old not in current:
            return {"ok": False, "error": "patch_conflict: old_text not found"}
        if args.get("replace_all"):
            updated = current.replace(old, new)
        else:
            updated = current.replace(old, new, 1)
        fs.write(path, updated)
        diff = make_line_diff(current, updated)
        additions = sum(1 for d in diff if d["type"] == "add")
        deletions = sum(1 for d in diff if d["type"] == "del")
        task = getattr(self, "_current_task", None)
        cid = self._note_change(task, path, "replaced", current, updated) if task else None
        return {
            "ok": True,
            "path": path,
            "change_id": cid,
            "file_changed": {
                "path": path,
                "change": "replaced",
                "change_id": cid,
                "additions": additions,
                "deletions": deletions,
            },
        }


    async def _rename_file(self, args: dict) -> dict:
        fs = self._fs()
        before = self._safe_read(args["path"])
        result = fs.rename(args["path"], args["new_path"])
        task = getattr(self, "_current_task", None)
        cid = None
        if task:
            cid = self._note_change(
                task, args["new_path"], "renamed", before, before,
                meta={"from": args["path"], "to": args["new_path"]},
            )
        return {
            "ok": True,
            **result,
            "change_id": cid,
            "file_changed": {
                "path": args["new_path"],
                "change": "renamed",
                "change_id": cid,
                "from": args["path"],
            },
        }


    async def _delete_file(self, args: dict) -> dict:
        fs = self._fs()
        before = self._safe_read(args["path"])
        fs.delete(args["path"])
        task = getattr(self, "_current_task", None)
        cid = self._note_change(task, args["path"], "deleted", before, None) if task else None
        return {
            "ok": True,
            "path": args["path"],
            "change_id": cid,
            "file_changed": {"path": args["path"], "change": "deleted", "change_id": cid},
        }


    async def _run_command(self, task: AgentTask, name: str, args: dict) -> dict:
        from execution.runner import run_command_in_project

        cmd = args.get("command")
        if not cmd:
            if name == "run_tests":
                path = args.get("path") or ""
                cmd = f"python -m pytest {path} -q" if path else "python -m pytest -q"
            elif name == "run_build":
                cmd = "npm run build"
            elif name == "run_linter":
                path = args.get("path") or "."
                cmd = f"python -m ruff check {path}"
            else:
                return {"ok": False, "error": "command required"}

        timeout = min(int(args.get("timeout_s") or 60), 120)
        # Prefer existing project-scoped runner if available; fall back to GitService-style subprocess
        try:
            if callable(run_command_in_project):
                result = await run_command_in_project(
                    self.user_id, self.project_id, cmd, timeout_s=timeout
                )
            else:
                result = await self._subprocess(cmd, timeout)
        except ImportError:
            result = await self._subprocess(cmd, timeout)
        except TypeError:
            result = await self._subprocess(cmd, timeout)

        ok = result.get("exit_code", 1) == 0
        event_type = "agent.test_result" if name == "run_tests" else "agent.command_output"
        return {
            "ok": ok,
            "command": cmd,
            "exit_code": result.get("exit_code"),
            "stdout": (result.get("stdout") or "")[:20_000],
            "stderr": (result.get("stderr") or "")[:10_000],
            "status": "succeeded" if ok else "failed",
            "_event_hint": event_type,
        }

    async def _subprocess(self, cmd: str, timeout: int) -> dict:
        from execution.files import PROJECTS_DIR
        root = (PROJECTS_DIR / self.user_id / self.project_id).resolve()
        root.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"exit_code": -1, "stdout": "", "stderr": "command timed out"}
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
        }

    async def _git(self, name: str, args: dict) -> dict:
        git = self._git_svc()
        if name == "git_status":
            data = await git.status()
            return {"ok": True, **data}
        if name == "git_diff":
            if hasattr(git, "diff"):
                data = await git.diff(path=args.get("path"), staged=bool(args.get("staged")))
            else:
                extra = ["--staged"] if args.get("staged") else []
                if args.get("path"):
                    extra += ["--", args["path"]]
                data = await git._run("diff", *extra)
            return {"ok": data.get("success", True), **data}
        if name == "git_log":
            limit = int(args.get("limit") or 20)
            if hasattr(git, "log"):
                data = await git.log(limit=limit)
            else:
                data = await git._run("log", f"-{limit}", "--oneline")
            return {"ok": data.get("success", True), **data}
        if name == "git_show":
            data = await git._run("show", args["ref"], "--stat")
            return {"ok": data.get("success", True), **data}
        if name == "git_branch":
            data = await git._run("branch", "-a")
            return {"ok": data.get("success", True), **data}
        if name == "git_add":
            paths = args.get("paths") or None
            data = await git.stage(paths)
            return {"ok": data.get("success", False), **data}
        if name == "git_commit":
            if hasattr(git, "commit"):
                data = await git.commit(args["message"])
            else:
                data = await git._run("commit", "-m", args["message"])
            return {"ok": data.get("success", False), **data}
        return {"ok": False, "error": f"unknown git tool {name}"}

    async def _diagnostics(self, name: str, args: dict) -> dict:
        # Best-effort against existing evidence/jobs APIs
        try:
            if name == "get_job":
                from core.database import ExecutionJob, async_session
                from sqlalchemy import select
                async with async_session() as db:
                    r = await db.execute(
                        select(ExecutionJob).where(ExecutionJob.id == args["job_id"])
                    )
                    job = r.scalar_one_or_none()
                    if not job:
                        return {"ok": False, "error": "job not found"}
                    if job.owner_id != self.user_id:
                        return {"ok": False, "error": "access denied"}
                    return {
                        "ok": True,
                        "job": {
                            "id": job.id,
                            "status": job.status,
                            "job_type": job.job_type,
                            "error": job.error,
                            "result": job.result,
                        },
                    }
            if name == "get_evidence":
                return {"ok": True, "evidence": [], "note": "use /api/evidence endpoints for full query"}
            if name == "get_job_logs":
                return {"ok": True, "logs": "", "job_id": args.get("job_id")}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "unavailable"}

    async def _workflows(self, name: str, args: dict) -> dict:
        try:
            from brain.workflow_store import WorkflowStore
            store = WorkflowStore()
            if name == "list_workflows":
                items = await store.list_for_user(self.user_id, limit=int(args.get("limit") or 20))
                return {"ok": True, "workflows": items}
            if name == "inspect_workflow":
                wf = await store.get(args["workflow_id"], user_id=self.user_id)
                if not wf:
                    return {"ok": False, "error": "not found"}
                return {"ok": True, "workflow": wf}
            if name == "execute_workflow":
                return {
                    "ok": False,
                    "error": "execute_workflow must be triggered via /api/workflow execute path with full governance",
                }
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "unavailable"}


    async def _repo_intel(self, name: str, args: dict) -> dict:
        fs = self._fs()
        tree = fs.tree()
        files = [i for i in tree if i.get("type") == "file"]

        if name == "get_project_metadata":
            langs = {}
            configs = []
            for f in files:
                path = f["path"]
                base = path.split("/")[-1].lower()
                ext = base.rsplit(".", 1)[-1] if "." in base else ""
                if ext:
                    langs[ext] = langs.get(ext, 0) + 1
                if base in (
                    "package.json", "pyproject.toml", "requirements.txt", "setup.py",
                    "cargo.toml", "go.mod", "makefile", "dockerfile", "pom.xml",
                    "tsconfig.json", "vite.config.ts", "vite.config.js",
                ):
                    configs.append(path)
            return {
                "ok": True,
                "file_count": len(files),
                "languages": dict(sorted(langs.items(), key=lambda x: -x[1])[:20]),
                "config_files": configs[:50],
            }

        if name == "get_test_files":
            max_r = int(args.get("max_results") or 50)
            tests = []
            for f in files:
                p = f["path"].lower()
                base = p.split("/")[-1]
                if (
                    "/test" in p or p.startswith("test") or base.startswith("test_")
                    or base.endswith("_test.py") or base.endswith(".test.js")
                    or base.endswith(".test.ts") or base.endswith(".spec.js")
                    or base.endswith(".spec.ts") or "/__tests__/" in p
                ):
                    tests.append(f["path"])
            return {"ok": True, "tests": tests[:max_r], "count": len(tests)}

        if name == "get_build_system":
            cmds = {}
            for candidate, keys in (
                ("package.json", ("scripts",)),
                ("pyproject.toml", ()),
                ("Makefile", ()),
                ("makefile", ()),
            ):
                match = next((f["path"] for f in files if f["path"].endswith(candidate) or f["path"] == candidate), None)
                if not match:
                    continue
                try:
                    content = fs.read(match).get("content") or ""
                except Exception:
                    continue
                if candidate == "package.json":
                    try:
                        import json as _json
                        data = _json.loads(content)
                        scripts = data.get("scripts") or {}
                        for k in ("test", "build", "lint", "typecheck", "format"):
                            if k in scripts:
                                cmds[k] = f"npm run {k}"
                    except Exception:
                        pass
                elif candidate.startswith("Makefile") or candidate == "makefile":
                    cmds.setdefault("build", "make")
                    cmds.setdefault("test", "make test")
                elif candidate == "pyproject.toml":
                    cmds.setdefault("test", "python -m pytest -q")
                    if "ruff" in content:
                        cmds.setdefault("lint", "python -m ruff check .")
            # requirements / pytest default
            if any(f["path"].endswith("pytest.ini") or "/tests/" in f["path"] for f in files):
                cmds.setdefault("test", "python -m pytest -q")
            return {"ok": True, "commands": cmds}

        if name == "get_package_dependencies":
            max_e = int(args.get("max_entries") or 100)
            deps = {}
            for f in files:
                base = f["path"].split("/")[-1]
                if base == "package.json":
                    try:
                        import json as _json
                        data = _json.loads(fs.read(f["path"]).get("content") or "{}")
                        for section in ("dependencies", "devDependencies"):
                            for k, v in (data.get(section) or {}).items():
                                deps[k] = str(v)
                    except Exception:
                        pass
                if base in ("requirements.txt", "requirements-lite.txt", "requirements-full.txt"):
                    try:
                        for line in (fs.read(f["path"]).get("content") or "").splitlines():
                            line = line.strip()
                            if line and not line.startswith("#"):
                                deps[line.split("==")[0].split(">=")[0].strip()] = line
                    except Exception:
                        pass
            items = list(deps.items())[:max_e]
            return {"ok": True, "dependencies": dict(items), "count": len(deps)}

        if name == "find_symbol":
            symbol = (args.get("symbol") or "").strip()
            if not symbol:
                return {"ok": False, "error": "symbol required"}
            max_r = int(args.get("max_results") or 20)
            patterns = [
                f"def {symbol}",
                f"class {symbol}",
                f"function {symbol}",
                f"const {symbol}",
                f"let {symbol}",
                f"var {symbol}",
                f"fn {symbol}",
                f"func {symbol}",
                symbol,
            ]
            hits = []
            for f in files:
                if f.get("is_binary"):
                    continue
                try:
                    content = fs.read(f["path"]).get("content") or ""
                except Exception:
                    continue
                for i, line in enumerate(content.splitlines(), 1):
                    for pat in patterns[:-1]:
                        if pat in line:
                            hits.append({"path": f["path"], "line": i, "text": line.strip()[:200]})
                            break
                    if len(hits) >= max_r:
                        break
                if len(hits) >= max_r:
                    break
            return {"ok": True, "symbol": symbol, "hits": hits}

        return {"ok": False, "error": f"unknown repo intel tool {name}"}

    async def _record_evidence(
        self, task: AgentTask, tool: AgentTool, args: dict, result: dict
    ) -> None:
        if tool.side_effect == SideEffect.NONE and not tool.durable:
            return
        try:
            from governance.evidence import record_evidence
            await record_evidence(
                actor_id=self.user_id,
                action=f"agent_tool:{tool.name}",
                details={
                    "task_id": task.id,
                    "correlation_id": task.correlation_id,
                    "project_id": self.project_id,
                    "tenant_id": self.tenant_id,
                    "owner_id": self.user_id,
                    "tool": tool.name,
                    "capability": tool.capability,
                    "side_effect": tool.side_effect.value,
                    "ok": result.get("ok"),
                    "args_keys": list(args.keys()),
                    "operation_id": result.get("operation_id"),
                    "outcome": "succeeded" if result.get("ok") else "failed",
                    "input_digest": result.get("input_digest"),
                    "target_digest": result.get("target_digest"),
                },
            )
        except Exception:
            # Evidence must not break the tool loop
            logger.debug("evidence record skipped", exc_info=True)


def _truncate_result(result: dict) -> dict:
    out = {}
    for k, v in result.items():
        if k.startswith("_"):
            continue
        if isinstance(v, str) and len(v) > 8000:
            out[k] = v[:8000] + "...[truncated]"
        elif isinstance(v, list) and len(v) > 100:
            out[k] = v[:100]
        else:
            out[k] = v
    return out
