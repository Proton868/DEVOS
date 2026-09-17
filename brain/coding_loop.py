"""Bounded iterative coding loop for the universal DevOS coding agent.

Stage machine (never infinite):

  INSPECT → PLAN → EDIT → EXECUTE → OBSERVE → DIAGNOSE
       ↑                                      │
       └──────── repair (within budget) ──────┘
                    ↓
              VALIDATE → EVIDENCE → ACCEPT

All file/command work must still go through governed tools (UCIP) when used
from AgentRuntime. This module is the orchestration contract + durable state;
it does not bypass authorization or enable fake runtime.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from brain.coding_foundation import evaluate_coding_validation


class CodingStage(str, Enum):
    INSPECT = "inspect"
    PLAN = "plan"
    EDIT = "edit"
    EXECUTE = "execute"
    OBSERVE = "observe"
    DIAGNOSE = "diagnose"
    VALIDATE = "validate"
    EVIDENCE = "evidence"
    ACCEPT = "accept"
    DONE = "done"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"


DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_WALL_S = 600


@dataclass
class CodingLoopConfig:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_wall_s: float = DEFAULT_MAX_WALL_S
    require_tests: bool = False
    require_build: bool = False
    require_evidence: bool = True

    def __post_init__(self) -> None:
        self.max_attempts = max(1, min(int(self.max_attempts), 20))
        self.max_wall_s = max(5.0, float(self.max_wall_s))


@dataclass
class CodingLoopState:
    """Durable-friendly snapshot of loop progress."""
    stage: str = CodingStage.INSPECT.value
    attempt: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    inspection: Optional[dict] = None
    plan: Optional[dict] = None
    files_changed: list = field(default_factory=list)
    last_command: Optional[dict] = None
    diagnosis: Optional[str] = None
    validation: Optional[dict] = None
    evidence_id: Optional[str] = None
    acceptance: Optional[dict] = None
    events: list = field(default_factory=list)
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "inspection": self.inspection,
            "plan": self.plan,
            "files_changed": list(self.files_changed or []),
            "last_command": self.last_command,
            "diagnosis": self.diagnosis,
            "validation": self.validation,
            "evidence_id": self.evidence_id,
            "acceptance": self.acceptance,
            "events": list(self.events),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": (self.finished_at or time.time()) - self.started_at,
        }


@dataclass
class CodingLoopHooks:
    """Injectable steps — production wires these to AgentRuntime/UCIP tools."""
    inspect: Callable[[], dict]
    plan: Callable[[dict], dict]
    edit: Callable[[dict, Optional[str]], list]
    execute: Callable[[dict], dict]
    diagnose: Callable[[dict], str]
    validate: Optional[Callable[[CodingLoopState], dict]] = None
    evidence: Optional[Callable[[CodingLoopState], Optional[str]]] = None
    accept: Optional[Callable[[CodingLoopState], dict]] = None
    is_cancelled: Optional[Callable[[], bool]] = None
    on_event: Optional[Callable[[dict], None]] = None


def _emit(state: CodingLoopState, stage: str, **data: Any) -> dict:
    ev = {"stage": stage, "attempt": state.attempt, "ts": time.time(), **data}
    state.events.append(ev)
    return ev


def run_coding_loop(
    hooks: CodingLoopHooks,
    config: Optional[CodingLoopConfig] = None,
    *,
    resume: Optional[CodingLoopState] = None,
) -> CodingLoopState:
    """Run the bounded coding loop. Never loops indefinitely.

    resume: optional durable state from interruption (continues attempt counter).
    """
    cfg = config or CodingLoopConfig()
    state = resume or CodingLoopState(max_attempts=cfg.max_attempts)
    state.max_attempts = cfg.max_attempts
    deadline = state.started_at + cfg.max_wall_s

    def notify(ev: dict) -> None:
        if hooks.on_event:
            try:
                hooks.on_event(ev)
            except Exception:
                pass

    def cancelled() -> bool:
        if hooks.is_cancelled and hooks.is_cancelled():
            return True
        return False

    def timed_out() -> bool:
        return time.time() >= deadline

    # INSPECT (once unless resume already past)
    if state.inspection is None:
        state.stage = CodingStage.INSPECT.value
        notify(_emit(state, CodingStage.INSPECT.value))
        if cancelled():
            state.stage = CodingStage.CANCELLED.value
            state.error = "cancelled"
            state.finished_at = time.time()
            return state
        state.inspection = hooks.inspect() or {}
        notify(_emit(state, CodingStage.INSPECT.value, status="done", inspection=state.inspection))

    # PLAN (once)
    if state.plan is None:
        state.stage = CodingStage.PLAN.value
        notify(_emit(state, CodingStage.PLAN.value))
        state.plan = hooks.plan(state.inspection) or {}
        notify(_emit(state, CodingStage.PLAN.value, status="done", plan=state.plan))

    while state.attempt < cfg.max_attempts:
        if cancelled():
            state.stage = CodingStage.CANCELLED.value
            state.error = "cancelled"
            state.finished_at = time.time()
            notify(_emit(state, CodingStage.CANCELLED.value))
            return state
        if timed_out():
            state.stage = CodingStage.EXHAUSTED.value
            state.error = "wall_clock_exhausted"
            state.finished_at = time.time()
            notify(_emit(state, CodingStage.EXHAUSTED.value, reason=state.error))
            return state

        state.attempt += 1
        attempt = state.attempt

        # EDIT
        state.stage = CodingStage.EDIT.value
        notify(_emit(state, CodingStage.EDIT.value, attempt=attempt))
        files = hooks.edit(state.plan, state.diagnosis) or []
        # Merge unique file paths
        existing = {
            (f.get("path") if isinstance(f, dict) else f)
            for f in state.files_changed
        }
        for f in files:
            path = f.get("path") if isinstance(f, dict) else f
            if path and path not in existing:
                state.files_changed.append(f if isinstance(f, dict) else {"path": f})
                existing.add(path)
        notify(_emit(state, CodingStage.EDIT.value, status="done", files=files))

        # EXECUTE
        state.stage = CodingStage.EXECUTE.value
        notify(_emit(state, CodingStage.EXECUTE.value, attempt=attempt))
        cmd = hooks.execute(state.plan) or {}
        state.last_command = cmd
        notify(_emit(state, CodingStage.EXECUTE.value, status="done", command=cmd))

        # OBSERVE
        state.stage = CodingStage.OBSERVE.value
        exit_code = int(cmd.get("exit_code", 1))
        cmd_ok = bool(cmd.get("ok")) and exit_code == 0
        notify(_emit(
            state,
            CodingStage.OBSERVE.value,
            exit_code=exit_code,
            ok=cmd_ok,
            stdout_tail=(cmd.get("stdout") or cmd.get("stdout_tail") or "")[-500:],
            stderr_tail=(cmd.get("stderr") or cmd.get("stderr_tail") or "")[-500:],
        ))

        if not cmd_ok:
            # DIAGNOSE → next attempt (if budget)
            state.stage = CodingStage.DIAGNOSE.value
            state.diagnosis = hooks.diagnose(cmd) or "execution_failed"
            notify(_emit(state, CodingStage.DIAGNOSE.value, diagnosis=state.diagnosis))
            if state.attempt >= cfg.max_attempts:
                break
            continue

        # VALIDATE
        state.stage = CodingStage.VALIDATE.value
        notify(_emit(state, CodingStage.VALIDATE.value))
        if hooks.validate:
            state.validation = hooks.validate(state)
        else:
            state.validation = evaluate_coding_validation(
                files_changed=state.files_changed,
                command_results=[cmd],
                tests_passed=cmd_ok if (state.plan or {}).get("validate_as") == "tests" else None,
                build_passed=cmd_ok if (state.plan or {}).get("validate_as") == "build" else None,
                require_tests=cfg.require_tests,
                require_build=cfg.require_build,
            )
        notify(_emit(state, CodingStage.VALIDATE.value, validation=state.validation))

        if not (state.validation or {}).get("ok"):
            state.diagnosis = (state.validation or {}).get("reasons") or "validation_failed"
            if isinstance(state.diagnosis, list):
                state.diagnosis = ",".join(str(x) for x in state.diagnosis)
            notify(_emit(state, CodingStage.DIAGNOSE.value, diagnosis=state.diagnosis))
            if state.attempt >= cfg.max_attempts:
                break
            continue

        # EVIDENCE
        state.stage = CodingStage.EVIDENCE.value
        notify(_emit(state, CodingStage.EVIDENCE.value))
        if hooks.evidence:
            state.evidence_id = hooks.evidence(state)
        notify(_emit(state, CodingStage.EVIDENCE.value, evidence_id=state.evidence_id))

        if cfg.require_evidence and not state.evidence_id:
            state.stage = CodingStage.FAILED.value
            state.error = "evidence_required_missing"
            state.finished_at = time.time()
            notify(_emit(state, CodingStage.FAILED.value, error=state.error))
            return state

        # ACCEPT
        state.stage = CodingStage.ACCEPT.value
        notify(_emit(state, CodingStage.ACCEPT.value))
        if hooks.accept:
            state.acceptance = hooks.accept(state)
        else:
            state.acceptance = {
                "ok": True,
                "reason": "validated",
                "evidence_id": state.evidence_id,
            }
        notify(_emit(state, CodingStage.ACCEPT.value, acceptance=state.acceptance))

        if (state.acceptance or {}).get("ok"):
            state.stage = CodingStage.DONE.value
            state.finished_at = time.time()
            notify(_emit(state, CodingStage.DONE.value))
            return state

        # Acceptance refused — count as failure for this attempt
        state.diagnosis = (state.acceptance or {}).get("reason") or "acceptance_denied"
        if state.attempt >= cfg.max_attempts:
            break

    state.stage = CodingStage.EXHAUSTED.value
    state.error = state.error or "retry_budget_exhausted"
    state.finished_at = time.time()
    notify(_emit(state, CodingStage.EXHAUSTED.value, reason=state.error, attempt=state.attempt))
    return state
