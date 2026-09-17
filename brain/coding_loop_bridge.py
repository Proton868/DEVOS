"""
Production bridge: CodingLoop drives specialist coding work on AgentRuntime.

Does NOT create a second runtime or declare mission success.
Mission acceptance (delegation / evaluate_mission_acceptance) remains authoritative.

Flow:
  INSPECT → PLAN → (EDIT → EXECUTE → OBSERVE → DIAGNOSE)* → VALIDATE → EVIDENCE → ACCEPT*
  * ACCEPT here is provisional coding-loop status only.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Optional

from brain.coding_loop import (
    CodingLoopConfig,
    CodingLoopState,
    CodingStage,
    DEFAULT_MAX_ATTEMPTS,
)
from brain.coding_foundation import inspect_workspace, evaluate_coding_validation

logger = logging.getLogger("devos.coding_loop_bridge")


def is_coding_objective(objective: str, persona_id: str = "") -> bool:
    """Whether this specialist node should run under CodingLoop control."""
    p = (persona_id or "").lower()
    if p in ("code", "web", "backend", "frontend", "full_stack", "fullstack", "python", "mobile"):
        return True
    o = (objective or "").lower()
    keys = (
        "implement", "fix", "refactor", "create", "build", "website",
        "test", "bug", "error", "file", "code", "function", "api",
        "html", "css", "python", "typescript", "vite", "next",
        "patch", "scaffold", "bootstrap",
    )
    return any(k in o for k in keys)


async def _run_agent_attempt(
    *,
    runtime,
    context,
    objective: str,
    plan: dict,
    diagnosis: Optional[str],
    attempt: int,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    """One bounded AgentRuntime pass for edit+execute (governed tools only)."""
    extra = ""
    if plan:
        extra += f"\nCoding plan: {plan.get('summary') or plan.get('goal') or plan}"
    if diagnosis:
        extra += f"\nPrior diagnosis (repair this): {diagnosis}"
    extra += (
        f"\nAttempt {attempt}. Use workspace tools (create_file/apply_patch/replace_text) "
        "and run_tests/run_command as needed. Do not claim success without verification."
    )
    full_obj = f"{objective}\n{extra}"

    files: list = []
    tools: list = []
    commands: list = []
    events: list = []
    provider_failure = None
    success = False
    status = "failed"
    summary = ""
    error = None
    task_id = None

    try:
        async for event in runtime.run(full_obj, context):
            if cancel_check and cancel_check():
                return {
                    "success": False,
                    "status": "cancelled",
                    "error": "cancelled",
                    "files_changed": files,
                    "tools_used": tools,
                    "commands": commands,
                    "events_seen": events,
                    "provider_failure": None,
                    "task_id": task_id,
                    "summary": "cancelled",
                }
            et = (event or {}).get("type") or ""
            data = (event or {}).get("data") or {}
            if event and event.get("task_id"):
                task_id = str(event["task_id"])
            if et:
                events.append(et)
            if et == "agent.completed":
                pf = data.get("provider_failure")
                if pf:
                    provider_failure = dict(pf) if isinstance(pf, dict) else {"detail": str(pf)}
                    success = False
                    status = "failed"
                    error = str(data.get("summary") or data.get("message") or "provider_exhausted")[:500]
                else:
                    success = data.get("success") is not False
                    status = "succeeded" if success else "failed"
                summary = str(data.get("summary") or "")[:2000]
                if data.get("files_changed"):
                    files = list(data.get("files_changed") or files)
            elif et in ("agent.tool_result", "agent.test_result", "agent.command_output"):
                tool = data.get("tool") or data.get("name")
                if tool and tool not in tools:
                    tools.append(str(tool))
                if data.get("files_changed"):
                    files = list(data.get("files_changed") or files)
                if data.get("command") or data.get("exit_code") is not None:
                    commands.append({
                        "command": data.get("command"),
                        "exit_code": data.get("exit_code"),
                        "ok": data.get("ok"),
                        "stdout": (data.get("stdout") or "")[-500:],
                        "stderr": (data.get("stderr") or "")[-500:],
                    })
                coding = data.get("coding") or {}
                if coding.get("command"):
                    commands.append({
                        "command": coding.get("command"),
                        "exit_code": coding.get("command_exit_code"),
                        "ok": coding.get("command_ok"),
                        "stdout": coding.get("command_stdout_tail") or "",
                        "stderr": coding.get("command_stderr_tail") or "",
                    })
            elif et in ("agent.error", "agent.agent_failed", "agent.agent_blocked"):
                success = False
                status = "blocked" if "blocked" in et else "failed"
                error = str(data.get("message") or data.get("summary") or et)[:500]
                if data.get("provider_failure"):
                    provider_failure = dict(data["provider_failure"])
            elif et == "agent.cancelled":
                success = False
                status = "cancelled"
                error = "cancelled"
    except Exception as e:
        if type(e).__name__ == "ProviderExhaustedError":
            provider_failure = e.public_dict() if hasattr(e, "public_dict") else {
                "category": getattr(e, "category", "rate_limited"),
                "retryable": getattr(e, "retryable", True),
                "http_status": getattr(e, "http_status", 429),
            }
            success = False
            status = "failed"
            error = str(e)[:500]
            return {
                "success": False,
                "status": "failed",
                "error": error,
                "files_changed": files,
                "tools_used": tools,
                "commands": commands,
                "last_command": commands[-1] if commands else {"ok": False, "exit_code": 1},
                "events_seen": events,
                "provider_failure": provider_failure,
                "task_id": task_id,
                "summary": error,
            }
        logger.exception("coding loop agent attempt failed")
        success = False
        status = "error"
        error = str(e)[:500]

    # Aggregate command outcome
    last_cmd = commands[-1] if commands else {
        "command": None,
        "exit_code": 0 if success else 1,
        "ok": success,
        "stdout": "",
        "stderr": error or "",
    }
    return {
        "success": success,
        "status": status,
        "error": error,
        "files_changed": files,
        "tools_used": tools,
        "commands": commands,
        "last_command": last_cmd,
        "events_seen": events,
        "provider_failure": provider_failure,
        "task_id": task_id,
        "summary": summary,
    }


async def run_production_coding_loop(
    *,
    runtime,
    context,
    objective: str,
    user_id: str,
    project_id: str,
    mission_id: Optional[str] = None,
    persona_id: str = "",
    agent_id: str = "",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_wall_s: float = 600.0,
    resume: Optional[CodingLoopState] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    on_event: Optional[Callable[[dict], None]] = None,
    require_tests: bool = False,
    require_build: bool = False,
) -> CodingLoopState:
    """
    Execute CodingLoop stages with AgentRuntime for EDIT/EXECUTE.

    Local ACCEPT is provisional. Callers must still run mission acceptance.
    """
    cfg = CodingLoopConfig(
        max_attempts=max_attempts,
        max_wall_s=max_wall_s,
        require_tests=require_tests,
        require_build=require_build,
        require_evidence=True,
    )
    state = resume or CodingLoopState(max_attempts=cfg.max_attempts)
    state.max_attempts = cfg.max_attempts
    deadline = state.started_at + cfg.max_wall_s

    def notify(stage: str, **data: Any) -> None:
        ev = {"stage": stage, "attempt": state.attempt, "ts": time.time(), **data}
        state.events.append(ev)
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass

    def cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    # INSPECT
    if state.inspection is None:
        state.stage = CodingStage.INSPECT.value
        notify(CodingStage.INSPECT.value)
        if cancelled():
            state.stage = CodingStage.CANCELLED.value
            state.error = "cancelled"
            state.finished_at = time.time()
            return state
        try:
            fs = runtime._fs()
            insp = inspect_workspace(fs)
            state.inspection = insp.to_dict() if hasattr(insp, "to_dict") else dict(insp or {})
        except Exception as e:
            state.inspection = {"error": type(e).__name__, "blank_project": True}
        notify(CodingStage.INSPECT.value, status="done", inspection=state.inspection)

    # PLAN
    if state.plan is None:
        state.stage = CodingStage.PLAN.value
        notify(CodingStage.PLAN.value)
        insp = state.inspection or {}
        tests = list(insp.get("suggested_test_commands") or [])
        builds = list(insp.get("suggested_build_commands") or [])
        state.plan = {
            "goal": (objective or "")[:500],
            "summary": f"coding mission for persona={persona_id or 'code'}",
            "suggested_tests": tests[:5],
            "suggested_builds": builds[:5],
            "validate_as": "tests" if tests else ("build" if builds else "files"),
            "ecosystem": insp.get("ecosystem"),
        }
        notify(CodingStage.PLAN.value, status="done", plan=state.plan)

    while state.attempt < cfg.max_attempts:
        if cancelled():
            state.stage = CodingStage.CANCELLED.value
            state.error = "cancelled"
            state.finished_at = time.time()
            notify(CodingStage.CANCELLED.value)
            return state
        if time.time() >= deadline:
            state.stage = CodingStage.EXHAUSTED.value
            state.error = "wall_clock_exhausted"
            state.finished_at = time.time()
            notify(CodingStage.EXHAUSTED.value, reason=state.error)
            return state

        state.attempt += 1
        attempt = state.attempt

        # EDIT + EXECUTE via governed AgentRuntime
        state.stage = CodingStage.EDIT.value
        notify(CodingStage.EDIT.value, attempt=attempt)
        attempt_result = await _run_agent_attempt(
            runtime=runtime,
            context=context,
            objective=objective,
            plan=state.plan or {},
            diagnosis=state.diagnosis,
            attempt=attempt,
            cancel_check=cancel_check,
        )
        if attempt_result.get("status") == "cancelled":
            state.stage = CodingStage.CANCELLED.value
            state.error = "cancelled"
            state.finished_at = time.time()
            notify(CodingStage.CANCELLED.value)
            return state

        # Merge files
        existing = {
            (f.get("path") if isinstance(f, dict) else f)
            for f in state.files_changed
        }
        for f in attempt_result.get("files_changed") or []:
            path = f.get("path") if isinstance(f, dict) else f
            if path and path not in existing:
                state.files_changed.append(f if isinstance(f, dict) else {"path": f})
                existing.add(path)
        notify(CodingStage.EDIT.value, status="done", files=attempt_result.get("files_changed"))

        state.stage = CodingStage.EXECUTE.value
        notify(CodingStage.EXECUTE.value, attempt=attempt)
        state.last_command = attempt_result.get("last_command")
        notify(CodingStage.EXECUTE.value, status="done", command=state.last_command)

        # OBSERVE
        state.stage = CodingStage.OBSERVE.value
        cmd = state.last_command or {}
        exit_code = int(cmd.get("exit_code") if cmd.get("exit_code") is not None else (0 if attempt_result.get("success") else 1))
        # Provider failure is hard observe failure
        if attempt_result.get("provider_failure"):
            cmd_ok = False
            state.error = attempt_result.get("error") or "provider_failure"
        else:
            cmd_ok = bool(attempt_result.get("success")) and exit_code == 0
        notify(
            CodingStage.OBSERVE.value,
            exit_code=exit_code,
            ok=cmd_ok,
            provider_failure=attempt_result.get("provider_failure"),
        )

        if not cmd_ok:
            state.stage = CodingStage.DIAGNOSE.value
            pf = attempt_result.get("provider_failure")
            if pf:
                state.diagnosis = f"provider_failure:{pf.get('category') or 'unknown'}"
                # Provider exhaustion: stop coding loop; do not burn remaining attempts
                state.stage = CodingStage.FAILED.value
                state.error = state.error or state.diagnosis
                state.finished_at = time.time()
                notify(CodingStage.FAILED.value, error=state.error, provider_failure=pf)
                # Attach for callers
                state.acceptance = {
                    "ok": False,
                    "reason": "provider_failure",
                    "provisional": True,
                    "mission_acceptance_required": True,
                }
                return state
            state.diagnosis = (
                attempt_result.get("error")
                or (cmd.get("stderr") or "")[:200]
                or "execution_failed"
            )
            notify(CodingStage.DIAGNOSE.value, diagnosis=state.diagnosis)
            if state.attempt >= cfg.max_attempts:
                break
            continue

        # VALIDATE
        state.stage = CodingStage.VALIDATE.value
        notify(CodingStage.VALIDATE.value)
        state.validation = evaluate_coding_validation(
            files_changed=state.files_changed,
            command_results=list(attempt_result.get("commands") or ([cmd] if cmd else [])),
            tests_passed=cmd_ok if (state.plan or {}).get("validate_as") == "tests" else None,
            build_passed=cmd_ok if (state.plan or {}).get("validate_as") == "build" else None,
            require_tests=cfg.require_tests,
            require_build=cfg.require_build,
        )
        # File-producing success without explicit test requirement: structure ok is enough for loop
        if not state.validation.get("ok") and state.files_changed and not cfg.require_tests and not cfg.require_build:
            state.validation = {
                "ok": True,
                "files_ok": True,
                "reasons": [],
                "note": "files_present_tests_not_required",
            }
        notify(CodingStage.VALIDATE.value, validation=state.validation)

        if not (state.validation or {}).get("ok"):
            state.diagnosis = ",".join(
                str(x) for x in ((state.validation or {}).get("reasons") or ["validation_failed"])
            )
            notify(CodingStage.DIAGNOSE.value, diagnosis=state.diagnosis)
            if state.attempt >= cfg.max_attempts:
                break
            continue

        # EVIDENCE (real coding evidence; not mission acceptance)
        state.stage = CodingStage.EVIDENCE.value
        notify(CodingStage.EVIDENCE.value)
        try:
            from brain.coding_evidence import build_coding_evidence, persist_coding_evidence
            ev = build_coding_evidence(
                mission_id=mission_id or project_id or "coding",
                project_id=project_id or "default",
                user_id=user_id,
                agent_id=agent_id or persona_id or "agent",
                persona_id=persona_id,
                files_changed=state.files_changed,
                commands=list(attempt_result.get("commands") or []),
                validation=state.validation,
                success=True,
            )
            state.evidence_id = persist_coding_evidence(ev)
        except Exception as e:
            logger.debug("coding evidence: %s", type(e).__name__)
            state.evidence_id = None
        notify(CodingStage.EVIDENCE.value, evidence_id=state.evidence_id)

        if cfg.require_evidence and not state.evidence_id:
            state.stage = CodingStage.FAILED.value
            state.error = "evidence_required_missing"
            state.finished_at = time.time()
            notify(CodingStage.FAILED.value, error=state.error)
            return state

        # ACCEPT — provisional only; mission gate is authoritative
        state.stage = CodingStage.ACCEPT.value
        notify(CodingStage.ACCEPT.value)
        state.acceptance = {
            "ok": True,
            "reason": "coding_loop_validated",
            "provisional": True,
            "mission_acceptance_required": True,
            "evidence_id": state.evidence_id,
        }
        notify(CodingStage.ACCEPT.value, acceptance=state.acceptance)
        state.stage = CodingStage.DONE.value
        state.finished_at = time.time()
        # Stash attempt metrics for NodeExecutionResult
        state.meta = {  # type: ignore[attr-defined]
            "tools_used": attempt_result.get("tools_used") or [],
            "commands": attempt_result.get("commands") or [],
            "task_id": attempt_result.get("task_id"),
            "summary": attempt_result.get("summary") or "",
        }
        notify(CodingStage.DONE.value)
        return state

    state.stage = CodingStage.EXHAUSTED.value
    state.error = state.error or "retry_budget_exhausted"
    state.finished_at = time.time()
    state.acceptance = {
        "ok": False,
        "reason": state.error,
        "provisional": True,
        "mission_acceptance_required": True,
    }
    notify(CodingStage.EXHAUSTED.value, reason=state.error, attempt=state.attempt)
    return state


def coding_loop_to_node_result(state: CodingLoopState) -> dict:
    """Map CodingLoopState → NodeExecutionResult fields (not mission success)."""
    meta = getattr(state, "meta", None) or {}
    provisional_ok = (
        state.stage == CodingStage.DONE.value
        and bool((state.acceptance or {}).get("ok"))
    )
    status = "succeeded" if provisional_ok else (
        "cancelled" if state.stage == CodingStage.CANCELLED.value else "failed"
    )
    return {
        "success": provisional_ok,
        "status": status,
        "summary": (meta.get("summary") or state.diagnosis or state.stage or "")[:2000],
        "files_changed": list(state.files_changed or []),
        "tools_used": list(meta.get("tools_used") or []),
        "commands": list(meta.get("commands") or []),
        "error": state.error,
        "events_seen": [e.get("stage") for e in (state.events or []) if isinstance(e, dict)],
        "task_id": meta.get("task_id"),
        "coding_loop": state.to_dict(),
        # Explicit: not mission acceptance
        "mission_acceptance_required": True,
        "provisional_acceptance": state.acceptance,
    }
