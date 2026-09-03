"""
HAI control plane for AgentRuntime (Stage 3J/3K).

Strategic/tactical intelligence — no tool execution, no second runtime.
HAI decides cognitive lifecycle; UCIP authorizes; ExecutionJob is truth.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from cognitive.hai_checkpoint import bound_state, build_checkpoint, HAICheckpoint


class StrategicDecision(str, Enum):
    CONTINUE = "continue"
    VERIFY = "verify"
    REPLAN = "replan"
    DELEGATE_COORDINATOR = "delegate_coordinator"
    DELEGATE_WORKFLOW = "delegate_workflow"
    BLOCK = "block"
    COMPLETE = "complete"
    FAIL = "fail"


CONSEQUENTIAL_EDIT_TOOLS = frozenset({
    "apply_patch", "replace_text", "create_file", "write_file",
    "rename_file", "delete_file",
})
VERIFY_TOOLS = frozenset({"run_tests", "run_command", "get_diagnostics"})

MAX_STRATEGIC_REPLANS = 6
MAX_TACTICAL_RECOVERIES = 3
MAX_SAME_ACTION_STREAK = 3
MAX_SAME_SUBGOAL_CYCLES = 4
MAX_DELEGATION_DEPTH = 2
MAX_TASK_DURATION_S = 900
MAX_VERIFY_LOOPS = 4


def _fingerprint(tool: str, args: Optional[dict], subgoal_id: Optional[str], outcome: str = "") -> str:
    """Meaningful action identity: tool + normalized args + subgoal + outcome class."""
    norm = {}
    if isinstance(args, dict):
        for k in sorted(args.keys()):
            if str(k).lower() in ("user_id", "tenant_id", "owner_id", "token", "secret"):
                continue
            v = args[k]
            if isinstance(v, (str, int, float, bool)) or v is None:
                norm[str(k)] = v if not isinstance(v, str) else v[:200]
            else:
                norm[str(k)] = str(v)[:200]
    raw = json.dumps({"t": tool, "a": norm, "sg": subgoal_id or "", "o": outcome}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def interpret_test_outcome(result: dict) -> Optional[bool]:
    """
    Test-specific interpreter for run_tests results.
    Returns True/False if determinable, None if inconclusive.
    Generic exit_code==0 alone is NOT sufficient.
    """
    if not isinstance(result, dict):
        return None
    if result.get("tests_passed") is True:
        return True
    if result.get("tests_passed") is False:
        return False
    if result.get("verification_passed") is True:
        return True
    if result.get("verification_passed") is False:
        return False

    out = str(result.get("stdout") or result.get("output") or "").lower()
    err = str(result.get("stderr") or result.get("error") or "").lower()
    blob = (out + "\n" + err).strip()

    code = result.get("exit_code")
    if code is None:
        code = result.get("returncode")
    try:
        code_i = int(code) if code is not None else None
    except Exception:
        code_i = None

    # Explicit failure signals
    if code_i is not None and code_i != 0:
        return False
    if re.search(r"\b\d+\s+failed\b", blob):
        return False
    if re.search(r"\b(failed|failure)\b", blob) and not re.search(r"\b\d+\s+passed\b", blob):
        if "error" in blob or "failed" in blob:
            return False

    # Explicit success signals (require test-like evidence, not empty ok)
    if re.search(r"\b\d+\s+passed\b", blob) and not re.search(r"\b\d+\s+failed\b", blob):
        return True
    if blob and "passed" in blob and "failed" not in blob and result.get("ok"):
        return True

    # Empty/ambiguous success — inconclusive
    return None


def interpret_command_verification(result: dict) -> Optional[bool]:
    """Generic run_command: exit_code==0 is NEVER enough."""
    if not isinstance(result, dict):
        return None
    if result.get("verification_passed") is True:
        return True
    if result.get("verification_passed") is False:
        return False
    if result.get("tests_passed") is True:
        return True
    if result.get("tests_passed") is False:
        return False
    # Prefer structured metadata only; do not invent verification from stdout heuristics
    return None


def interpret_diagnostics_verification(result: dict) -> Optional[bool]:
    """get_diagnostics: tool ok is not proof the repo is clean."""
    if not isinstance(result, dict):
        return None
    if result.get("verification_passed") is True:
        return True
    if result.get("verification_passed") is False:
        return False
    if result.get("diagnostics_clean") is True:
        return True
    if result.get("diagnostics_clean") is False:
        return False
    # Explicit error counts if provided by existing tool contract
    if "error_count" in result:
        try:
            return int(result["error_count"]) == 0
        except Exception:
            return None
    return None


def interpret_verification_outcome(tool: str, result: dict) -> Optional[bool]:
    """
    Authoritative dispatcher: tool type matters.
    True = verification evidence proves success
    False = verification evidence proves failure
    None = not verification evidence / inconclusive
    """
    name = (tool or "").strip()
    if name == "run_tests":
        return interpret_test_outcome(result if isinstance(result, dict) else {})
    if name == "run_command":
        return interpret_command_verification(result if isinstance(result, dict) else {})
    if name == "get_diagnostics":
        return interpret_diagnostics_verification(result if isinstance(result, dict) else {})
    return None


@dataclass
class Subgoal:
    id: str
    description: str
    depends_on: list = field(default_factory=list)
    status: str = "pending"
    attempts: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "description": self.description,
            "depends_on": list(self.depends_on), "status": self.status, "attempts": self.attempts,
        }


@dataclass
class HAIControl:
    objective: str = ""
    constraints: list = field(default_factory=list)
    completion_criteria: list = field(default_factory=list)
    subgoals: list = field(default_factory=list)
    current_subgoal_id: Optional[str] = None
    plan_version: int = 0
    lifecycle: str = "created"
    replan_count: int = 0
    iteration_count: int = 0
    uncertainty: float = 0.3
    action_fingerprints: list = field(default_factory=list)  # recent fingerprints
    failure_history: list = field(default_factory=list)
    last_decision: str = ""
    last_reason_code: str = ""
    verification_status: str = "not_started"  # not_started|required|passed|failed
    verification_required: bool = False
    consequential_edit_pending: bool = False
    verify_loop_count: int = 0
    delegation_depth: int = 0
    tactical_recovery_count: int = 0
    started_at: float = field(default_factory=time.monotonic)
    last_job_id: Optional[str] = None
    last_job_status: Optional[str] = None
    workflow_id: Optional[str] = None
    plan_dirty: bool = False  # True after REPLAN until consumed by runtime
    evidence_refs: list = field(default_factory=list)

    def to_state_dict(self) -> dict:
        return bound_state({
            "strategic": {
                "objective": self.objective,
                "constraints": list(self.constraints)[:16],
                "completion_criteria": list(self.completion_criteria)[:12],
                "plan": "\n".join(f"- [{s.id}] {s.description}" for s in self.subgoals),
                "plan_version": self.plan_version,
                "current_subgoal_id": self.current_subgoal_id,
                "subgoals": [s.to_dict() for s in self.subgoals],
                "completed_subgoals": [s.id for s in self.subgoals if s.status == "completed"],
                "blockers": [],
                "status": self.lifecycle,
                "iteration_count": self.iteration_count,
                "replan_count": self.replan_count,
                "uncertainty": self.uncertainty,
            },
            "tactical": {
                "current_subgoal_id": self.current_subgoal_id,
                "action_fingerprints": list(self.action_fingerprints)[-48:],
                "failure_history": list(self.failure_history)[-16:],
                "verification_status": self.verification_status,
                "verification_required": self.verification_required,
                "consequential_edit_pending": self.consequential_edit_pending,
            },
            "verification": {
                "status": self.verification_status,
                "required": self.verification_required,
            },
            "lifecycle": self.lifecycle,
        })

    def checkpoint(self, task_id: str, correlation_id: str = "", state_version: int = 0) -> HAICheckpoint:
        return build_checkpoint(
            task_id=task_id, state=self.to_state_dict(), lifecycle=self.lifecycle,
            state_version=state_version, last_job_id=self.last_job_id,
            last_job_status=self.last_job_status, workflow_id=self.workflow_id,
            correlation_id=correlation_id,
        )

    def can_complete(self) -> tuple[bool, str]:
        """Real completion gate — not LLM text."""
        if self.lifecycle in ("blocked", "failed", "cancelled", "unknown", "replanning"):
            return False, f"lifecycle={self.lifecycle}"
        if self.verification_required or self.verification_status == "required":
            return False, "verification_outstanding"
        if self.consequential_edit_pending:
            return False, "consequential_edit_unverified"
        if self.verification_status == "failed":
            return False, "verification_failed"
        if self.lifecycle == "replanning" or self.plan_dirty:
            return False, "replan_pending"
        incomplete = [s for s in self.subgoals if s.status not in ("completed", "skipped")]
        # Allow complete if only summarize remains and verification passed for code tasks
        codeish = any(k in (self.objective or "").lower() for k in ("fix", "bug", "implement", "auth", "test", "edit"))
        if codeish and self.verification_status != "passed" and any(
            s.id.startswith("sg_verify") or "verify" in s.id for s in self.subgoals
        ):
            if self.verification_status != "passed":
                return False, "code_change_not_verified"
        if incomplete:
            # If only summarize left and verified, allow
            if len(incomplete) == 1 and "summarize" in incomplete[0].id and self.verification_status in ("passed", "not_started"):
                if codeish and self.verification_status != "passed" and self.consequential_edit_pending is False:
                    if self.verification_status == "passed":
                        return True, "ok"
                    # read-only objectives
                    if not any(s.id in ("sg_patch", "sg_implement", "sg_act") and s.status == "completed" for s in self.subgoals):
                        return True, "ok"
                if self.verification_status == "passed" or not codeish:
                    return True, "ok"
            return False, f"incomplete_subgoals:{[s.id for s in incomplete]}"
        if all(s.status == "completed" for s in self.subgoals) or not self.subgoals:
            if codeish and self.verification_status not in ("passed", "not_started"):
                if self.verification_status != "passed":
                    return False, "verification_not_passed"
            return True, "ok"
        return False, "criteria_unmet"


def derive_subgoals(objective: str) -> list[Subgoal]:
    """Deterministic strategic decomposition. Seam: replaceable by GoalDecomposer later."""
    low = (objective or "").lower()
    steps: list[tuple[str, str]] = [("sg_orient", "Orient on repository and relevant files")]
    if any(k in low for k in ("test", "auth", "login", "bug", "fix", "fail")):
        steps += [
            ("sg_reproduce", "Reproduce / locate the failing behavior"),
            ("sg_patch", "Apply a minimal correct fix"),
            ("sg_verify", "Run relevant tests and verify"),
        ]
    elif any(k in low for k in ("implement", "add", "create", "feature", "edit", "change")):
        steps += [
            ("sg_design", "Outline the change"),
            ("sg_implement", "Implement the change"),
            ("sg_verify", "Verify the change"),
        ]
    else:
        steps += [("sg_act", "Execute primary work"), ("sg_verify", "Verify outcome")]
    steps.append(("sg_summarize", "Summarize results"))
    out = []
    for i, (sid, desc) in enumerate(steps):
        out.append(Subgoal(
            id=sid, description=desc,
            depends_on=[steps[i - 1][0]] if i else [],
            status="ready" if i == 0 else "pending",
        ))
    return out


def completion_criteria_for(objective: str) -> list[str]:
    crit = [f"Objective addressed: {(objective or '')[:160]}"]
    low = (objective or "").lower()
    if any(k in low for k in ("test", "auth", "bug", "fix", "implement", "edit")):
        crit.append("Relevant verification evidence (tests/build/diagnostics) recorded")
    crit.append("Structured summary produced")
    return crit


def classify_delegation(objective: str) -> StrategicDecision:
    low = (objective or "").lower()
    if re.search(r"\b(workflow|pipeline|cron|scheduled)\b", low):
        return StrategicDecision.DELEGATE_WORKFLOW
    if re.search(r"\b(multi[- ]worker|parallel workers|coordinator|team of agents)\b", low):
        return StrategicDecision.DELEGATE_COORDINATOR
    return StrategicDecision.CONTINUE


@dataclass
class StrategicController:
    control: HAIControl = field(default_factory=HAIControl)

    def start(self, objective: str) -> dict:
        self.control.objective = objective
        self.control.completion_criteria = completion_criteria_for(objective)
        self.control.subgoals = derive_subgoals(objective)
        self.control.plan_version = 1
        self.control.lifecycle = "planning"
        self.control.iteration_count = 1
        self.control.plan_dirty = False
        delegation = classify_delegation(objective)
        if delegation != StrategicDecision.CONTINUE:
            self.control.last_decision = delegation.value
            self.control.last_reason_code = "delegation_signal"
            self.control.lifecycle = "executing"
            return self._out(delegation, "delegation_signal", f"Delegate: {delegation.value}")
        sel = self._select_next()
        self.control.current_subgoal_id = sel.id if sel else None
        if sel:
            sel.status = "in_progress"
        self.control.lifecycle = "executing"
        return self._out(StrategicDecision.CONTINUE, "initial_plan", "Plan created")

    def on_tool_result(
        self,
        tool: str,
        ok: bool,
        summary: str = "",
        *,
        arguments: Optional[dict] = None,
        result: Optional[dict] = None,
    ) -> dict:
        result = result or {}
        outcome = "ok" if ok else "error"
        if tool in VERIFY_TOOLS:
            passed = interpret_verification_outcome(tool, result)
            if passed is True:
                outcome = "tests_passed"
            elif passed is False:
                outcome = "tests_failed"
            elif ok:
                outcome = "cmd_ok_no_test_signal"
            else:
                outcome = "cmd_failed"

        fp = _fingerprint(tool, arguments, self.control.current_subgoal_id, outcome)
        self.control.action_fingerprints.append(fp)
        if len(self.control.action_fingerprints) > 64:
            self.control.action_fingerprints = self.control.action_fingerprints[-64:]
        self.control.iteration_count += 1

        bound = self._bounds()
        if bound:
            return bound

        # Repeated identical action (same tool+args+subgoal+outcome class)
        fps = self.control.action_fingerprints
        if len(fps) >= MAX_SAME_ACTION_STREAK and fps[-1] == fps[-2] == fps[-3]:
            self.control.lifecycle = "blocked"
            return self._out(StrategicDecision.BLOCK, "repeated_action_loop", "Identical action repeated")

        if tool in CONSEQUENTIAL_EDIT_TOOLS and ok:
            self.control.consequential_edit_pending = True
            self.control.verification_required = True
            self.control.verification_status = "required"
            return self._out(StrategicDecision.VERIFY, "verification_required", "Edit applied; verification required")

        if tool in VERIFY_TOOLS:
            passed = interpret_verification_outcome(tool, result)
            # Count only unresolved/inconclusive or failed verification attempts toward loop limit
            if passed is not True:
                self.control.verify_loop_count += 1
            if self.control.verify_loop_count > MAX_VERIFY_LOOPS:
                self.control.lifecycle = "blocked"
                return self._out(StrategicDecision.BLOCK, "max_verify_loops", "Verification loop limit")
            if passed is True:
                self.control.verification_status = "passed"
                self.control.verification_required = False
                self.control.consequential_edit_pending = False
                return self._complete_subgoal_or_continue("tests_passed")
            if passed is False:
                self.control.verification_status = "failed"
                self.control.failure_history.append(f"{tool}:tests_failed:{summary[:200]}")
                self.control.tactical_recovery_count += 1
                if self.control.tactical_recovery_count > MAX_TACTICAL_RECOVERIES:
                    return self._replan("verification_failed_exhausted")
                return self._out(StrategicDecision.CONTINUE, "verification_failed", "Tests failed; continue/replan")
            # Command ran but no clear test signal
            if ok:
                # Do not treat as verification pass
                return self._out(StrategicDecision.VERIFY, "verification_inconclusive", "No clear test pass signal")
            self.control.failure_history.append(f"{tool}:{summary[:200]}")
            return self._out(StrategicDecision.CONTINUE, "verify_tool_failed", summary or "verify tool failed")

        if not ok:
            self.control.failure_history.append(f"{tool}:{summary[:200]}")
            self.control.tactical_recovery_count += 1
            if self.control.tactical_recovery_count > MAX_TACTICAL_RECOVERIES:
                return self._replan("tactical_recovery_exhausted")
            return self._out(StrategicDecision.CONTINUE, "tactical_recovery", "Local recovery")

        self.control.tactical_recovery_count = 0
        return self._out(StrategicDecision.CONTINUE, "tactical_continue", "Continue")

    def evaluate_natural_language_completion(self, text: str = "") -> dict:
        """
        LLM final answer without tool call — HAI may veto premature success.
        """
        ok, reason = self.control.can_complete()
        if not ok:
            if self.control.verification_required or self.control.consequential_edit_pending:
                return self._out(StrategicDecision.VERIFY, reason, "Verification required before completion")
            if self.control.lifecycle in ("blocked", "failed"):
                return self._out(StrategicDecision.BLOCK, reason, "Cannot complete")
            return self._out(StrategicDecision.CONTINUE, reason, "Completion criteria not met")
        self.control.lifecycle = "succeeded"
        return self._out(StrategicDecision.COMPLETE, "objective_complete", (text or "Complete")[:500])

    def consume_plan_update(self) -> Optional[dict]:
        """Return new plan context after REPLAN for injection into the next reasoning cycle."""
        if not self.control.plan_dirty:
            return None
        self.control.plan_dirty = False
        return {
            "plan_version": self.control.plan_version,
            "current_subgoal": self.control.current_subgoal_id,
            "subgoals": [s.to_dict() for s in self.control.subgoals],
            "completion_criteria": list(self.control.completion_criteria),
            "verification_status": self.control.verification_status,
        }

    def _complete_subgoal_or_continue(self, reason: str) -> dict:
        cur = self.control.current_subgoal_id
        if cur:
            for s in self.control.subgoals:
                if s.id == cur:
                    s.status = "completed"
        # Also complete verify subgoal when tests passed
        if reason == "tests_passed":
            for s in self.control.subgoals:
                if "verify" in s.id and s.status != "completed":
                    s.status = "completed"
        can, why = self.control.can_complete()
        if can and all(s.status == "completed" for s in self.control.subgoals):
            self.control.lifecycle = "succeeded"
            return self._out(StrategicDecision.COMPLETE, "objective_complete", reason)
        nxt = self._select_next()
        if not nxt:
            if can:
                self.control.lifecycle = "succeeded"
                return self._out(StrategicDecision.COMPLETE, "objective_complete", reason)
            self.control.lifecycle = "blocked"
            return self._out(StrategicDecision.BLOCK, "no_subgoal", why)
        nxt.status = "in_progress"
        nxt.attempts += 1
        self.control.current_subgoal_id = nxt.id
        return self._out(StrategicDecision.CONTINUE, "advance_subgoal", f"Next {nxt.id}")

    def _select_next(self) -> Optional[Subgoal]:
        done = {s.id for s in self.control.subgoals if s.status == "completed"}
        for s in self.control.subgoals:
            if s.status == "completed":
                continue
            if all(d in done for d in s.depends_on):
                return s
        return None

    def _replan(self, reason: str) -> dict:
        self.control.replan_count += 1
        if self.control.replan_count > MAX_STRATEGIC_REPLANS:
            self.control.lifecycle = "blocked"
            return self._out(StrategicDecision.BLOCK, "max_replans", "Replan budget exhausted")
        self.control.lifecycle = "replanning"
        self.control.plan_version += 1
        self.control.plan_dirty = True
        done = {s.id for s in self.control.subgoals if s.status == "completed"}
        for s in self.control.subgoals:
            if s.id not in done:
                s.status = "pending"
        sel = self._select_next()
        if sel:
            sel.status = "in_progress"
            self.control.current_subgoal_id = sel.id
        self.control.lifecycle = "executing"
        self.control.tactical_recovery_count = 0
        self.control.verification_required = False
        return self._out(StrategicDecision.REPLAN, reason, f"Replan v{self.control.plan_version}")

    def _bounds(self) -> Optional[dict]:
        if time.monotonic() - self.control.started_at > MAX_TASK_DURATION_S:
            self.control.lifecycle = "failed"
            return self._out(StrategicDecision.FAIL, "max_duration", "Task duration limit")
        if self.control.delegation_depth > MAX_DELEGATION_DEPTH:
            self.control.lifecycle = "blocked"
            return self._out(StrategicDecision.BLOCK, "max_delegation_depth", "Delegation depth")
        return None

    def _out(self, decision: StrategicDecision, reason_code: str, message: str) -> dict:
        self.control.last_decision = decision.value
        self.control.last_reason_code = reason_code
        return {
            "decision": decision.value,
            "reason_code": reason_code,
            "message": message,
            "selected_subgoal": self.control.current_subgoal_id,
            "plan_version": self.control.plan_version,
            "lifecycle": self.control.lifecycle,
            "completion_criteria": list(self.control.completion_criteria),
            "subgoals": [s.to_dict() for s in self.control.subgoals],
            "verification_status": self.control.verification_status,
            "verification_required": self.control.verification_required,
            "can_complete": self.control.can_complete()[0],
        }
