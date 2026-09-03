"""
HAI control plane for AgentRuntime (Stage 3J).

Strategic/tactical intelligence — no tool execution, no second runtime.
Uses GoalDecomposer-compatible subgoal structure; Coordinator remains
deterministic multi-worker orchestration and is only *delegated to*.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from cognitive.hai_checkpoint import (
    build_checkpoint,
    bound_state,
    reconcile_with_execution,
    HAICheckpoint,
)


class StrategicDecision(str, Enum):
    CONTINUE = "continue"
    VERIFY = "verify"
    REPLAN = "replan"
    DELEGATE_COORDINATOR = "delegate_coordinator"
    DELEGATE_WORKFLOW = "delegate_workflow"
    BLOCK = "block"
    COMPLETE = "complete"
    FAIL = "fail"


# Bounds (loop prevention)
MAX_STRATEGIC_REPLANS = 6
MAX_TACTICAL_RECOVERIES = 3
MAX_SAME_ACTION_STREAK = 3
MAX_SAME_SUBGOAL_CYCLES = 4
MAX_DELEGATION_DEPTH = 2
MAX_TASK_DURATION_S = 900


@dataclass
class Subgoal:
    id: str
    description: str
    depends_on: list = field(default_factory=list)
    status: str = "pending"  # pending|ready|in_progress|completed|failed|blocked
    attempts: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "attempts": self.attempts,
        }


@dataclass
class HAIControl:
    """Bounded HAI state attached to one AgentTask."""
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
    action_history: list = field(default_factory=list)
    failure_history: list = field(default_factory=list)
    last_decision: str = ""
    last_reason_code: str = ""
    verification_status: str = "not_started"
    delegation_depth: int = 0
    tactical_recovery_count: int = 0
    started_at: float = field(default_factory=time.monotonic)
    last_job_id: Optional[str] = None
    last_job_status: Optional[str] = None
    workflow_id: Optional[str] = None

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
                "current_action": self.action_history[-1] if self.action_history else "",
                "action_count": len(self.action_history),
                "action_history": list(self.action_history)[-48:],
                "failure_history": list(self.failure_history)[-16:],
                "verification_status": self.verification_status,
            },
            "verification": {"status": self.verification_status},
            "lifecycle": self.lifecycle,
        })

    def checkpoint(self, task_id: str, correlation_id: str = "", state_version: int = 0) -> HAICheckpoint:
        return build_checkpoint(
            task_id=task_id,
            state=self.to_state_dict(),
            lifecycle=self.lifecycle,
            state_version=state_version,
            last_job_id=self.last_job_id,
            last_job_status=self.last_job_status,
            workflow_id=self.workflow_id,
            correlation_id=correlation_id,
        )


def derive_subgoals(objective: str) -> list[Subgoal]:
    """Deterministic strategic decomposition (no LLM required)."""
    low = (objective or "").lower()
    steps: list[tuple[str, str]] = [("sg_orient", "Orient on repository and relevant files")]
    if any(k in low for k in ("test", "auth", "login", "bug", "fix", "fail")):
        steps += [
            ("sg_reproduce", "Reproduce / locate the failing behavior"),
            ("sg_patch", "Apply a minimal correct fix"),
            ("sg_verify", "Run relevant tests and verify"),
        ]
    elif any(k in low for k in ("implement", "add", "create", "feature")):
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
            id=sid,
            description=desc,
            depends_on=[steps[i - 1][0]] if i else [],
            status="ready" if i == 0 else "pending",
        ))
    return out


def completion_criteria_for(objective: str) -> list[str]:
    crit = [f"Objective addressed: {(objective or '')[:160]}"]
    low = (objective or "").lower()
    if any(k in low for k in ("test", "auth", "bug", "fix")):
        crit.append("Relevant tests pass or failure is explained with evidence")
    if any(k in low for k in ("implement", "fix", "edit", "change")):
        crit.append("Required code change applied and verified")
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
        delegation = classify_delegation(objective)
        if delegation != StrategicDecision.CONTINUE:
            self.control.last_decision = delegation.value
            self.control.last_reason_code = "delegation_signal"
            self.control.lifecycle = "executing"
            return self._out(delegation, "delegation_signal", f"Delegate: {delegation.value}")
        # Select first ready subgoal
        sel = self._select_next()
        self.control.current_subgoal_id = sel.id if sel else None
        if sel:
            sel.status = "in_progress"
        self.control.lifecycle = "executing"
        self.control.last_decision = StrategicDecision.CONTINUE.value
        self.control.last_reason_code = "initial_plan"
        return self._out(StrategicDecision.CONTINUE, "initial_plan", "Plan created")

    def on_tool_result(self, tool: str, ok: bool, summary: str = "") -> dict:
        self.control.action_history.append(tool)
        self.control.iteration_count += 1
        bound = self._bounds()
        if bound:
            return bound

        # Repeated action loop
        hist = self.control.action_history
        if len(hist) >= MAX_SAME_ACTION_STREAK and hist[-1] == hist[-2] == hist[-3]:
            self.control.last_decision = StrategicDecision.BLOCK.value
            self.control.last_reason_code = "repeated_action_loop"
            self.control.lifecycle = "blocked"
            return self._out(StrategicDecision.BLOCK, "repeated_action_loop", "Same action repeated")

        if not ok:
            self.control.failure_history.append(f"{tool}:{summary[:200]}")
            self.control.tactical_recovery_count += 1
            if self.control.tactical_recovery_count > MAX_TACTICAL_RECOVERIES:
                return self._replan("tactical_recovery_exhausted")
            return self._out(StrategicDecision.CONTINUE, "tactical_recovery", "Local recovery")

        self.control.tactical_recovery_count = 0
        # Verification signals
        if tool in ("run_tests", "run_command", "get_diagnostics") and ok:
            self.control.verification_status = "passed"
            return self._complete_subgoal_or_continue("verified")

        if tool in ("apply_patch", "replace_text", "create_file", "write_file") and ok:
            self.control.verification_status = "in_progress"
            return self._out(StrategicDecision.VERIFY, "verification_required", "Edit applied; verify")

        return self._out(StrategicDecision.CONTINUE, "tactical_continue", "Continue")

    def mark_subgoal_complete(self, reason: str = "done") -> dict:
        return self._complete_subgoal_or_continue(reason)

    def _complete_subgoal_or_continue(self, reason: str) -> dict:
        cur = self.control.current_subgoal_id
        if cur:
            for s in self.control.subgoals:
                if s.id == cur:
                    s.status = "completed"
        if all(s.status == "completed" for s in self.control.subgoals):
            self.control.lifecycle = "succeeded"
            self.control.last_decision = StrategicDecision.COMPLETE.value
            self.control.last_reason_code = "objective_complete"
            return self._out(StrategicDecision.COMPLETE, "objective_complete", reason)
        nxt = self._select_next()
        if not nxt:
            self.control.lifecycle = "blocked"
            return self._out(StrategicDecision.BLOCK, "no_subgoal", "No ready subgoal")
        # Detect subgoal churn
        if sum(1 for s in self.control.subgoals if s.attempts > MAX_SAME_SUBGOAL_CYCLES) > 0:
            return self._out(StrategicDecision.BLOCK, "subgoal_cycle", "Subgoal cycle")
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
        # Reset non-completed subgoals to pending/ready
        done = {s.id for s in self.control.subgoals if s.status == "completed"}
        for s in self.control.subgoals:
            if s.id not in done:
                s.status = "pending"
        sel = self._select_next()
        if sel:
            sel.status = "ready"
            self.control.current_subgoal_id = sel.id
            sel.status = "in_progress"
        self.control.lifecycle = "executing"
        self.control.tactical_recovery_count = 0
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
        }
