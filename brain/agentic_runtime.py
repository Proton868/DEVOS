"""Durable bounded agentic automation runtime.

Persisted state machine + turn loop around the governed agentic contract.
No second executor, job queue, or UCIP path.

reason → inspect → select capability → authorize → execute (async substrate)
  → observe → checkpoint → reason …

Consequential work always goes through CapabilitySubstrate / ExecutionOperation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from brain.agentic_automation import (
    AgentTaskLifecycle,
    CapabilityRequestRecord,
    DelegationError,
    GovernedAgentTask,
    assert_owner,
    get_agent_task_store,
    mark_completed,
    mark_failed,
    mark_unknown,
    reject_fabricated_evidence,
    request_capability,
    record_plan,
)

logger = logging.getLogger("devos.agentic_runtime")


# ── Persisted state machine (authoritative names) ─────────────────────────────

class AgentRuntimeState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_CAPABILITY = "awaiting_capability"
    AUTHORIZING = "authorizing"
    AUTHORIZED = "authorized"
    EXECUTING = "executing"
    OBSERVING = "observing"
    CHECKPOINTING = "checkpointing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


# Map legacy AgentTaskLifecycle → runtime state
_LEGACY_MAP = {
    AgentTaskLifecycle.PENDING: AgentRuntimeState.CREATED,
    AgentTaskLifecycle.PLANNING: AgentRuntimeState.PLANNING,
    AgentTaskLifecycle.AWAITING_CAPABILITY: AgentRuntimeState.AWAITING_CAPABILITY,
    AgentTaskLifecycle.AUTHORIZED: AgentRuntimeState.AUTHORIZED,
    AgentTaskLifecycle.RUNNING: AgentRuntimeState.EXECUTING,
    AgentTaskLifecycle.VERIFYING: AgentRuntimeState.OBSERVING,
    AgentTaskLifecycle.COMPLETED: AgentRuntimeState.COMPLETED,
    AgentTaskLifecycle.FAILED: AgentRuntimeState.FAILED,
    AgentTaskLifecycle.CANCELLED: AgentRuntimeState.CANCELLED,
    AgentTaskLifecycle.UNKNOWN: AgentRuntimeState.UNKNOWN,
    AgentTaskLifecycle.BLOCKED: AgentRuntimeState.BLOCKED,
}


TERMINAL_RUNTIME = frozenset({
    AgentRuntimeState.COMPLETED,
    AgentRuntimeState.FAILED,
    AgentRuntimeState.CANCELLED,
})

# Exceptional — not ordinary failure
EXCEPTIONAL = frozenset({AgentRuntimeState.UNKNOWN})

# Legal transitions (fail-closed)
_TRANSITIONS: dict[AgentRuntimeState, frozenset[AgentRuntimeState]] = {
    AgentRuntimeState.CREATED: frozenset({
        AgentRuntimeState.PLANNING, AgentRuntimeState.CANCELLED, AgentRuntimeState.FAILED,
    }),
    AgentRuntimeState.PLANNING: frozenset({
        AgentRuntimeState.AWAITING_CAPABILITY, AgentRuntimeState.COMPLETED,
        AgentRuntimeState.BLOCKED, AgentRuntimeState.FAILED, AgentRuntimeState.CANCELLED,
    }),
    AgentRuntimeState.AWAITING_CAPABILITY: frozenset({
        AgentRuntimeState.AUTHORIZING, AgentRuntimeState.BLOCKED, AgentRuntimeState.CANCELLED,
    }),
    AgentRuntimeState.AUTHORIZING: frozenset({
        AgentRuntimeState.AUTHORIZED, AgentRuntimeState.BLOCKED,
        AgentRuntimeState.FAILED, AgentRuntimeState.CANCELLED,
    }),
    AgentRuntimeState.AUTHORIZED: frozenset({
        AgentRuntimeState.EXECUTING, AgentRuntimeState.CANCELLED,
    }),
    AgentRuntimeState.EXECUTING: frozenset({
        AgentRuntimeState.OBSERVING, AgentRuntimeState.UNKNOWN,
        AgentRuntimeState.FAILED, AgentRuntimeState.CANCELLED,
    }),
    AgentRuntimeState.OBSERVING: frozenset({
        AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.FAILED, AgentRuntimeState.BLOCKED,
    }),
    AgentRuntimeState.CHECKPOINTING: frozenset({
        AgentRuntimeState.PLANNING, AgentRuntimeState.COMPLETED,
        AgentRuntimeState.BLOCKED, AgentRuntimeState.FAILED,
    }),
    AgentRuntimeState.UNKNOWN: frozenset({
        AgentRuntimeState.BLOCKED, AgentRuntimeState.OBSERVING,  # OBSERVING only after reconcile
    }),
    AgentRuntimeState.BLOCKED: frozenset({
        AgentRuntimeState.PLANNING, AgentRuntimeState.CANCELLED, AgentRuntimeState.FAILED,
    }),
    AgentRuntimeState.COMPLETED: frozenset(),
    AgentRuntimeState.FAILED: frozenset(),
    AgentRuntimeState.CANCELLED: frozenset(),
}


class IllegalTransition(Exception):
    def __init__(self, frm: AgentRuntimeState, to: AgentRuntimeState):
        super().__init__(f"illegal agent runtime transition {frm.value} → {to.value}")
        self.frm = frm
        self.to = to


def can_transition(frm: AgentRuntimeState | str, to: AgentRuntimeState | str) -> bool:
    a = AgentRuntimeState(str(frm)) if not isinstance(frm, AgentRuntimeState) else frm
    b = AgentRuntimeState(str(to)) if not isinstance(to, AgentRuntimeState) else to
    if a == b:
        return True
    return b in _TRANSITIONS.get(a, frozenset())


def transition(frm: AgentRuntimeState, to: AgentRuntimeState) -> AgentRuntimeState:
    if not can_transition(frm, to):
        raise IllegalTransition(frm, to)
    return to


# ── Bounds (runtime-enforced; agent cannot raise) ─────────────────────────────

DEFAULT_MAX_TURNS = 8
HARD_MAX_TURNS = 32
DEFAULT_MAX_CAPS_PER_TURN = 1
HARD_MAX_CAPS_PER_TURN = 3
DEFAULT_MAX_CAPS_PER_TASK = 16
HARD_MAX_CAPS_PER_TASK = 64
MAX_OBSERVATION_CHARS = 8_000
MAX_PLAN_CHARS = 4_000
MAX_CONTEXT_CHARS = 32_000


def max_turns() -> int:
    try:
        n = int(os.environ.get("DEVOS_AGENT_MAX_TURNS", str(DEFAULT_MAX_TURNS)))
    except Exception:
        n = DEFAULT_MAX_TURNS
    return max(1, min(n, HARD_MAX_TURNS))


def max_caps_per_turn() -> int:
    try:
        n = int(os.environ.get("DEVOS_AGENT_MAX_CAPS_PER_TURN", str(DEFAULT_MAX_CAPS_PER_TURN)))
    except Exception:
        n = DEFAULT_MAX_CAPS_PER_TURN
    return max(1, min(n, HARD_MAX_CAPS_PER_TURN))


def max_caps_per_task() -> int:
    try:
        n = int(os.environ.get("DEVOS_AGENT_MAX_CAPS_PER_TASK", str(DEFAULT_MAX_CAPS_PER_TASK)))
    except Exception:
        n = DEFAULT_MAX_CAPS_PER_TASK
    return max(1, min(n, HARD_MAX_CAPS_PER_TASK))


def bounds_policy() -> dict:
    return {
        "max_turns": max_turns(),
        "max_caps_per_turn": max_caps_per_turn(),
        "max_caps_per_task": max_caps_per_task(),
        "max_observation_chars": MAX_OBSERVATION_CHARS,
        "max_plan_chars": MAX_PLAN_CHARS,
        "max_context_chars": MAX_CONTEXT_CHARS,
        "agent_cannot_raise": True,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bound_str(s: Any, n: int) -> str:
    t = str(s or "")
    return t if len(t) <= n else t[:n] + "…[truncated]"


def _scrub_context(ctx: dict) -> dict:
    """Remove secret-like keys from agent context."""
    out = {}
    deny = ("password", "secret", "token", "api_key", "credential", "private_key", "authorization")
    for k, v in (ctx or {}).items():
        lk = str(k).lower()
        if any(d in lk for d in deny):
            continue
        if isinstance(v, dict):
            out[k] = _scrub_context(v)
        elif isinstance(v, str):
            out[k] = _bound_str(v, 2000)
        else:
            out[k] = v
    return out


@dataclass
class AgentCheckpoint:
    """Durable resume snapshot for the agentic runtime."""

    task_id: str
    owner_id: str
    tenant_id: Optional[str] = None
    parent_run_id: Optional[str] = None
    workflow_version: Optional[int] = None
    agent_type: str = "worker"
    state: AgentRuntimeState = AgentRuntimeState.CREATED
    turn: int = 0
    max_turns: int = field(default_factory=max_turns)
    allowed_capabilities: list[str] = field(default_factory=list)
    pending_request: Optional[dict] = None
    last_observation: Optional[dict] = None
    plan: Optional[dict] = None
    operation_id: Optional[str] = None
    job_id: Optional[str] = None
    evidence_refs: list = field(default_factory=list)
    result_refs: list = field(default_factory=list)
    capability_request_count: int = 0
    cancel_requested: bool = False
    unknown_info: Optional[dict] = None
    failure: Optional[str] = None
    version: int = 1
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "owner_id": self.owner_id,
            "tenant_id": self.tenant_id,
            "parent_run_id": self.parent_run_id,
            "workflow_version": self.workflow_version,
            "agent_type": self.agent_type,
            "state": self.state.value if isinstance(self.state, AgentRuntimeState) else self.state,
            "turn": self.turn,
            "max_turns": self.max_turns,
            "allowed_capabilities": list(self.allowed_capabilities or []),
            "pending_request": self.pending_request,
            "last_observation": self.last_observation,
            "plan": self.plan,
            "operation_id": self.operation_id,
            "job_id": self.job_id,
            "evidence_refs": list(self.evidence_refs or []),
            "result_refs": list(self.result_refs or []),
            "capability_request_count": self.capability_request_count,
            "cancel_requested": self.cancel_requested,
            "unknown_info": self.unknown_info,
            "failure": self.failure,
            "version": self.version,
            "updated_at": self.updated_at,
            "bounds": bounds_policy(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentCheckpoint":
        st = d.get("state") or AgentRuntimeState.CREATED.value
        try:
            state = AgentRuntimeState(str(st))
        except Exception:
            state = AgentRuntimeState.CREATED
        return cls(
            task_id=str(d.get("task_id") or ""),
            owner_id=str(d.get("owner_id") or ""),
            tenant_id=d.get("tenant_id"),
            parent_run_id=d.get("parent_run_id"),
            workflow_version=d.get("workflow_version"),
            agent_type=str(d.get("agent_type") or "worker"),
            state=state,
            turn=int(d.get("turn") or 0),
            max_turns=min(int(d.get("max_turns") or max_turns()), HARD_MAX_TURNS),
            allowed_capabilities=list(d.get("allowed_capabilities") or []),
            pending_request=d.get("pending_request"),
            last_observation=d.get("last_observation"),
            plan=d.get("plan"),
            operation_id=d.get("operation_id"),
            job_id=d.get("job_id"),
            evidence_refs=list(d.get("evidence_refs") or []),
            result_refs=list(d.get("result_refs") or []),
            capability_request_count=int(d.get("capability_request_count") or 0),
            cancel_requested=bool(d.get("cancel_requested")),
            unknown_info=d.get("unknown_info"),
            failure=d.get("failure"),
            version=int(d.get("version") or 1),
            updated_at=str(d.get("updated_at") or _now()),
        )


def checkpoint_from_task(task: GovernedAgentTask) -> AgentCheckpoint:
    """Build checkpoint from governed task (plus recovery dict)."""
    rt = (task.recovery or {}).get("runtime_checkpoint")
    if isinstance(rt, dict) and rt.get("task_id"):
        return AgentCheckpoint.from_dict(rt)
    legacy = _LEGACY_MAP.get(task.status, AgentRuntimeState.CREATED)
    return AgentCheckpoint(
        task_id=task.task_id,
        owner_id=task.owner_id,
        tenant_id=task.tenant_id,
        parent_run_id=task.parent_run_id,
        workflow_version=task.workflow_version,
        agent_type=task.agent_type,
        state=legacy,
        allowed_capabilities=list(task.allowed_capabilities or []),
        plan=task.plan,
        evidence_refs=list(task.evidence_refs or []),
        operation_id=(task.operation_ids or [None])[-1] if task.operation_ids else None,
        job_id=(task.job_ids or [None])[-1] if task.job_ids else None,
    )


def persist_checkpoint(task: GovernedAgentTask, cp: AgentCheckpoint) -> GovernedAgentTask:
    cp.version = int(cp.version or 1) + 1
    cp.updated_at = _now()
    task.recovery = dict(task.recovery or {})
    task.recovery["runtime_checkpoint"] = cp.to_dict()
    # Mirror coarse status onto AgentTaskLifecycle for projections
    _mirror_lifecycle(task, cp.state)
    return get_agent_task_store().put(task)


def _mirror_lifecycle(task: GovernedAgentTask, state: AgentRuntimeState) -> None:
    mapping = {
        AgentRuntimeState.CREATED: AgentTaskLifecycle.PENDING,
        AgentRuntimeState.PLANNING: AgentTaskLifecycle.PLANNING,
        AgentRuntimeState.AWAITING_CAPABILITY: AgentTaskLifecycle.AWAITING_CAPABILITY,
        AgentRuntimeState.AUTHORIZING: AgentTaskLifecycle.AWAITING_CAPABILITY,
        AgentRuntimeState.AUTHORIZED: AgentTaskLifecycle.AUTHORIZED,
        AgentRuntimeState.EXECUTING: AgentTaskLifecycle.RUNNING,
        AgentRuntimeState.OBSERVING: AgentTaskLifecycle.VERIFYING,
        AgentRuntimeState.CHECKPOINTING: AgentTaskLifecycle.VERIFYING,
        AgentRuntimeState.COMPLETED: AgentTaskLifecycle.COMPLETED,
        AgentRuntimeState.FAILED: AgentTaskLifecycle.FAILED,
        AgentRuntimeState.CANCELLED: AgentTaskLifecycle.CANCELLED,
        AgentRuntimeState.BLOCKED: AgentTaskLifecycle.BLOCKED,
        AgentRuntimeState.UNKNOWN: AgentTaskLifecycle.UNKNOWN,
    }
    task.status = mapping.get(state, task.status)


def apply_transition(cp: AgentCheckpoint, to: AgentRuntimeState) -> AgentCheckpoint:
    cp.state = transition(cp.state, to)
    cp.updated_at = _now()
    return cp


def build_agent_context(task: GovernedAgentTask, cp: AgentCheckpoint) -> dict:
    """Bounded deterministic context for a reasoning turn."""
    ctx = {
        "task_id": task.task_id,
        "state": cp.state.value,
        "turn": cp.turn,
        "max_turns": cp.max_turns,
        "allowed_capabilities": list(cp.allowed_capabilities or []),
        "parent_run_id": cp.parent_run_id,
        "last_observation": cp.last_observation,
        "plan": cp.plan,
        "evidence_refs": list(cp.evidence_refs or [])[:20],
        "operation_id": cp.operation_id,
        "job_id": cp.job_id,
        "capability_request_count": cp.capability_request_count,
        "cancel_requested": cp.cancel_requested,
        "bounds": bounds_policy(),
    }
    raw = json.dumps(ctx, default=str)
    if len(raw) > MAX_CONTEXT_CHARS:
        ctx["last_observation"] = {"truncated": True}
        ctx["_context_truncated"] = True
    return _scrub_context(ctx)


def capability_request_idempotency_key(
    *,
    task_id: str,
    turn: int,
    capability_id: str,
    occurrence: int = 1,
) -> str:
    raw = json.dumps(
        {"task_id": task_id, "turn": turn, "capability_id": capability_id, "occurrence": occurrence},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class TurnDecision:
    """Structured agent decision — not free-form proof."""
    kind: str  # capability_request | complete | block | fail
    capability_id: Optional[str] = None
    inputs: dict = field(default_factory=dict)
    reason: str = ""
    complete: bool = False

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "capability_id": self.capability_id,
            "inputs": dict(self.inputs or {}),
            "reason": _bound_str(self.reason, 500),
            "complete": self.complete,
        }


# Optional planner: callable(context) -> TurnDecision
PlannerFn = Callable[[dict], TurnDecision]

@dataclass
class CompletionContract:
    """Immutable completion requirements. Agent cannot rewrite at runtime."""

    required_successful_capabilities: int = 0
    required_evidence: bool = False
    required_outputs: list[str] = field(default_factory=list)
    all_operations_must_succeed: bool = True
    require_structured_complete_decision: bool = True
    # Optional explicit capability ids that must have succeeded
    required_capability_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "required_successful_capabilities": self.required_successful_capabilities,
            "required_evidence": self.required_evidence,
            "required_outputs": list(self.required_outputs or []),
            "all_operations_must_succeed": self.all_operations_must_succeed,
            "require_structured_complete_decision": self.require_structured_complete_decision,
            "required_capability_ids": list(self.required_capability_ids or []),
            "immutable": True,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "CompletionContract":
        d = d or {}
        return cls(
            required_successful_capabilities=int(d.get("required_successful_capabilities") or 0),
            required_evidence=bool(d.get("required_evidence")),
            required_outputs=list(d.get("required_outputs") or []),
            all_operations_must_succeed=bool(d.get("all_operations_must_succeed", True)),
            require_structured_complete_decision=bool(d.get("require_structured_complete_decision", True)),
            required_capability_ids=list(d.get("required_capability_ids") or []),
        )


@dataclass
class CompletionValidation:
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reasons": list(self.reasons)}


def get_completion_contract(task: GovernedAgentTask) -> CompletionContract:
    raw = (task.task_input or {}).get("completion_contract") or (task.authorization or {}).get("completion_contract")
    if isinstance(raw, dict):
        return CompletionContract.from_dict(raw)
    # Default: require structured decision only (no consequential floor)
    return CompletionContract()


def set_completion_contract(task: GovernedAgentTask, contract: CompletionContract) -> GovernedAgentTask:
    """Bind contract once; reject later mutation attempts."""
    existing = (task.task_input or {}).get("completion_contract")
    if isinstance(existing, dict) and existing.get("immutable"):
        # already bound — ignore rewrite
        return task
    task.task_input = dict(task.task_input or {})
    task.task_input["completion_contract"] = contract.to_dict()
    return get_agent_task_store().put(task)


def _successful_capability_count(task: GovernedAgentTask, cp: AgentCheckpoint) -> int:
    n = 0
    for rec in task.capability_requests or []:
        if getattr(rec, "status", None) in ("executed", "authorized") and not getattr(rec, "error", None):
            # Prefer executed with evidence when required later
            if rec.status == "executed":
                n += 1
    # Observations with status executed/succeeded
    obs = cp.last_observation or {}
    if obs.get("status") in ("executed", "succeeded") and n == 0:
        n = max(n, 1)
    # Count distinct evidence-backed ops from recovery history
    hist = (task.recovery or {}).get("observation_history") or []
    for h in hist:
        if isinstance(h, dict) and h.get("status") in ("executed", "succeeded"):
            n = max(n, n)  # counted via hist length below
    if hist:
        n = max(n, sum(1 for h in hist if isinstance(h, dict) and h.get("status") in ("executed", "succeeded")))
    return n


def validate_completion(
    task: GovernedAgentTask,
    *,
    decision: Optional[TurnDecision] = None,
    cp: Optional[AgentCheckpoint] = None,
) -> CompletionValidation:
    """Authoritative completion gate. Free-form text is never sufficient."""
    reasons: list[str] = []
    cp = cp or checkpoint_from_task(task)
    contract = get_completion_contract(task)

    if cp.cancel_requested or cp.state == AgentRuntimeState.CANCELLED:
        reasons.append("task_cancelled")
    if cp.state == AgentRuntimeState.UNKNOWN:
        reasons.append("state_unknown")
    if cp.unknown_info:
        reasons.append("unknown_info_present")
    if cp.pending_request and cp.state not in (AgentRuntimeState.PLANNING, AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.OBSERVING):
        # pending during planning after observe is cleared; only block if still active
        if cp.state in (AgentRuntimeState.AWAITING_CAPABILITY, AgentRuntimeState.AUTHORIZING, AgentRuntimeState.AUTHORIZED, AgentRuntimeState.EXECUTING):
            reasons.append("pending_capability_request")
    if cp.state == AgentRuntimeState.EXECUTING:
        reasons.append("operation_still_active")

    # Structured decision required
    if contract.require_structured_complete_decision:
        if decision is None or not (decision.kind == "complete" or decision.complete):
            reasons.append("missing_structured_complete_decision")
        # Free-form alone is never enough — reject pure textual claims without kind=complete
        if decision and decision.kind not in ("complete",) and decision.complete is not True:
            reasons.append("invalid_completion_kind")

    # Capability floor
    succ = _successful_capability_count(task, cp)
    if contract.required_successful_capabilities > 0:
        if succ < contract.required_successful_capabilities:
            reasons.append(
                f"insufficient_successful_capabilities:{succ}<{contract.required_successful_capabilities}"
            )

    if contract.required_capability_ids:
        done = set()
        for rec in task.capability_requests or []:
            if rec.status == "executed":
                done.add(rec.capability_id)
        for hid in (task.recovery or {}).get("observation_history") or []:
            if isinstance(hid, dict) and hid.get("status") in ("executed", "succeeded"):
                done.add(str(hid.get("capability_id") or ""))
        for req in contract.required_capability_ids:
            if req not in done:
                reasons.append(f"missing_capability_success:{req}")

    if contract.required_evidence:
        if not (cp.evidence_refs or task.evidence_refs):
            # Allow substrate metadata evidence on observations
            hist = (task.recovery or {}).get("observation_history") or []
            has_ev = any(
                isinstance(h, dict) and h.get("evidence_refs") for h in hist
            )
            if not has_ev:
                reasons.append("missing_evidence")

    if contract.required_outputs:
        result = task.result or {}
        outputs = (result.get("outputs") if isinstance(result.get("outputs"), dict) else result) or {}
        for key in contract.required_outputs:
            if key not in outputs and key not in result:
                reasons.append(f"missing_required_output:{key}")

    # Linked UNKNOWN ops
    for rec in task.capability_requests or []:
        if rec.status == "unknown":
            reasons.append("capability_request_unknown")

    ok = len(reasons) == 0
    return CompletionValidation(ok=ok, reasons=reasons)


def try_complete(
    task: GovernedAgentTask,
    cp: AgentCheckpoint,
    decision: TurnDecision,
) -> tuple[GovernedAgentTask, AgentCheckpoint, bool]:
    """Validate then COMPLETED, or return to PLANNING/BLOCKED without completing."""
    v = validate_completion(task, decision=decision, cp=cp)
    task.recovery = dict(task.recovery or {})
    task.recovery["last_completion_validation"] = v.to_dict()
    if not v.ok:
        # Do not complete — stay in PLANNING or BLOCKED
        if "task_cancelled" in v.reasons or "state_unknown" in v.reasons:
            if "state_unknown" in v.reasons:
                try:
                    apply_transition(cp, AgentRuntimeState.BLOCKED)
                except IllegalTransition:
                    cp.state = AgentRuntimeState.BLOCKED
            cp.failure = ";".join(v.reasons)
            persist_checkpoint(task, cp)
            return task, cp, False
        # Soft fail: remain PLANNING for another turn
        if cp.state != AgentRuntimeState.PLANNING:
            try:
                if cp.state in (AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.OBSERVING):
                    apply_transition(cp, AgentRuntimeState.PLANNING)
            except IllegalTransition:
                cp.state = AgentRuntimeState.PLANNING
        else:
            # already planning — record rejection only
            pass
        cp.failure = "completion_rejected:" + ",".join(v.reasons)
        persist_checkpoint(task, cp)
        return task, cp, False

    # Test-only crash seam: after validation, before durable COMPLETED.
    # Activated only when DEVOS_AGENT_CRASH_AFTER_COMPLETION_VALIDATION is exactly "1".
    # Production must never set this; documented in agentic-automation-runtime.md.
    if os.environ.get("DEVOS_AGENT_CRASH_AFTER_COMPLETION_VALIDATION") == "1":
        task.recovery = dict(task.recovery or {})
        task.recovery["last_completion_validation"] = v.to_dict()
        task.recovery["crash_after_completion_validation"] = True
        # Persist validation outcome only — state remains non-COMPLETED
        persist_checkpoint(task, cp)
        raise RuntimeError("DEVOS_AGENT_CRASH_AFTER_COMPLETION_VALIDATION")

    apply_transition(cp, AgentRuntimeState.COMPLETED)
    persist_checkpoint(task, cp)
    mark_completed(
        task,
        result={
            "complete": True,
            "turn": cp.turn,
            "plan_is_not_evidence": True,
            "evidence_refs": list(cp.evidence_refs),
            "completion_validation": v.to_dict(),
            "outputs": dict((task.result or {}).get("outputs") or {}),
        },
        evidence_refs=list(cp.evidence_refs),
    )
    return task, cp, True




def default_planner(context: dict) -> TurnDecision:
    """Deterministic planner for tests / no-LLM path.

    If a capability is allowed and no observation yet, request first allowlisted
    capability once; else complete.
    """
    caps = list(context.get("allowed_capabilities") or [])
    obs = context.get("last_observation")
    turn = int(context.get("turn") or 0)
    if obs or turn >= 1:
        return TurnDecision(kind="complete", complete=True, reason="structured_complete")
    if caps:
        return TurnDecision(
            kind="capability_request",
            capability_id=str(caps[0]),
            inputs={},
            reason="request_first_delegated_capability",
        )
    return TurnDecision(kind="block", reason="no_capabilities_delegated")


def request_cancel_runtime(task: GovernedAgentTask) -> GovernedAgentTask:
    cp = checkpoint_from_task(task)
    cp.cancel_requested = True
    if cp.state in (
        AgentRuntimeState.CREATED, AgentRuntimeState.PLANNING,
        AgentRuntimeState.AWAITING_CAPABILITY, AgentRuntimeState.AUTHORIZED,
        AgentRuntimeState.OBSERVING, AgentRuntimeState.CHECKPOINTING,
        AgentRuntimeState.BLOCKED,
    ):
        try:
            apply_transition(cp, AgentRuntimeState.CANCELLED)
        except IllegalTransition:
            cp.state = AgentRuntimeState.CANCELLED
    persist_checkpoint(task, cp)
    return task


async def run_agent_turn(
    task: GovernedAgentTask,
    *,
    planner: Optional[PlannerFn] = None,
    execute_capability: bool = True,
) -> GovernedAgentTask:
    """Execute one durable turn of the agentic runtime.

    Synchronous: planning / context / validation.
    Asynchronous boundary: consequential capability via request_capability
    (substrate → operation/job). Worker is not held beyond the substrate call;
    long-running jobs return linked ids and state EXECUTING for later resume.
    """
    assert_owner(task, task.owner_id, task.tenant_id)
    cp = checkpoint_from_task(task)
    planner = planner or default_planner

    if cp.cancel_requested and cp.state not in TERMINAL_RUNTIME:
        try:
            apply_transition(cp, AgentRuntimeState.CANCELLED)
        except IllegalTransition:
            cp.state = AgentRuntimeState.CANCELLED
        persist_checkpoint(task, cp)
        task.status = AgentTaskLifecycle.CANCELLED
        return get_agent_task_store().put(task)

    if cp.state in TERMINAL_RUNTIME:
        return task

    if cp.state == AgentRuntimeState.UNKNOWN:
        # Cannot auto-continue consequential work
        try:
            apply_transition(cp, AgentRuntimeState.BLOCKED)
        except IllegalTransition:
            pass
        cp.failure = cp.failure or "unknown_pending_review"
        persist_checkpoint(task, cp)
        return task

    # Start → PLANNING
    if cp.state == AgentRuntimeState.CREATED:
        apply_transition(cp, AgentRuntimeState.PLANNING)
        persist_checkpoint(task, cp)

    if cp.state not in (
        AgentRuntimeState.PLANNING,
        AgentRuntimeState.OBSERVING,
        AgentRuntimeState.CHECKPOINTING,
        AgentRuntimeState.AUTHORIZED,
        AgentRuntimeState.AWAITING_CAPABILITY,
    ):
        # EXECUTING waits for external completion signal via observe_operation_result
        return task

    if cp.state == AgentRuntimeState.CHECKPOINTING:
        # Continue to next planning after checkpoint
        apply_transition(cp, AgentRuntimeState.PLANNING)
        persist_checkpoint(task, cp)

    if cp.state == AgentRuntimeState.OBSERVING:
        apply_transition(cp, AgentRuntimeState.CHECKPOINTING)
        persist_checkpoint(task, cp)
        apply_transition(cp, AgentRuntimeState.PLANNING)
        persist_checkpoint(task, cp)

    # PLANNING
    if cp.state != AgentRuntimeState.PLANNING:
        return task

    cp.turn += 1
    if cp.turn > min(cp.max_turns, max_turns()):
        apply_transition(cp, AgentRuntimeState.BLOCKED)
        cp.failure = "maximum_agent_turns_reached"
        persist_checkpoint(task, cp)
        task.status = AgentTaskLifecycle.BLOCKED
        task.error = "maximum_agent_turns_reached"
        return get_agent_task_store().put(task)

    if cp.capability_request_count >= max_caps_per_task():
        apply_transition(cp, AgentRuntimeState.BLOCKED)
        cp.failure = "maximum_capability_requests_reached"
        persist_checkpoint(task, cp)
        return task

    context = build_agent_context(task, cp)
    decision = planner(context)
    # Untrusted planner output — re-validate structured contract
    try:
        from brain.agentic_llm_planner import parse_structured_plan, validate_plan_against_context, PlannerValidationError
        _plan = parse_structured_plan(decision)
        _plan = validate_plan_against_context(_plan, context)
        decision = _plan.to_turn_decision()
    except PlannerValidationError as _pve:
        decision = TurnDecision(kind="block", reason=f"planner_invalid:{_pve}")
    except Exception:
        pass  # non-LLM TurnDecision path stays as-is when already valid
    # Bound plan size
    plan = decision.to_dict()
    plan_s = json.dumps(plan, default=str)
    if len(plan_s) > MAX_PLAN_CHARS:
        plan = {"kind": decision.kind, "truncated": True}
    record_plan(task, plan)
    cp.plan = plan

    if decision.kind == "complete" or decision.complete:
        task, cp, ok = try_complete(task, cp, decision)
        return task

    if decision.kind == "block":
        apply_transition(cp, AgentRuntimeState.BLOCKED)
        cp.failure = decision.reason or "blocked"
        persist_checkpoint(task, cp)
        return task

    if decision.kind == "fail":
        apply_transition(cp, AgentRuntimeState.FAILED)
        cp.failure = decision.reason or "failed"
        persist_checkpoint(task, cp)
        mark_failed(task, cp.failure)
        return task

    if decision.kind != "capability_request" or not decision.capability_id:
        apply_transition(cp, AgentRuntimeState.BLOCKED)
        cp.failure = "invalid_turn_decision"
        persist_checkpoint(task, cp)
        return task

    # Bound: at most N caps per turn (default 1)
    apply_transition(cp, AgentRuntimeState.AWAITING_CAPABILITY)
    cp.pending_request = {
        "capability_id": decision.capability_id,
        "inputs": dict(decision.inputs or {}),
        "idempotency_key": capability_request_idempotency_key(
            task_id=task.task_id,
            turn=cp.turn,
            capability_id=str(decision.capability_id),
        ),
    }
    persist_checkpoint(task, cp)

    # AUTHORIZING
    apply_transition(cp, AgentRuntimeState.AUTHORIZING)
    persist_checkpoint(task, cp)

    cid = str(decision.capability_id)
    if cid not in (cp.allowed_capabilities or []):
        apply_transition(cp, AgentRuntimeState.BLOCKED)
        cp.failure = "capability_not_delegated"
        persist_checkpoint(task, cp)
        return task

    # request_capability enforces UCIP; dry_run first then execute
    try:
        rec = await request_capability(
            task,
            capability_id=cid,
            inputs=dict(decision.inputs or {}),
            execute=False,
        )
    except DelegationError as e:
        apply_transition(cp, AgentRuntimeState.FAILED)
        cp.failure = e.message
        persist_checkpoint(task, cp)
        mark_failed(task, e.message)
        return task

    if rec.status == "denied":
        apply_transition(cp, AgentRuntimeState.BLOCKED)
        cp.failure = rec.error or "capability_denied"
        persist_checkpoint(task, cp)
        return task

    apply_transition(cp, AgentRuntimeState.AUTHORIZED)
    persist_checkpoint(task, cp)

    if not execute_capability:
        return task

    # EXECUTING — consequential path via substrate
    apply_transition(cp, AgentRuntimeState.EXECUTING)
    cp.capability_request_count += 1
    persist_checkpoint(task, cp)

    try:
        rec2 = await request_capability(
            task,
            capability_id=cid,
            inputs=dict(decision.inputs or {}),
            execute=True,
        )
    except DelegationError as e:
        if "UNKNOWN" in e.code:
            apply_transition(cp, AgentRuntimeState.UNKNOWN)
            cp.unknown_info = {"reason": e.message, "auto_retry": False}
            persist_checkpoint(task, cp)
            mark_unknown(task, reason=e.message)
            return task
        apply_transition(cp, AgentRuntimeState.FAILED)
        cp.failure = e.message
        persist_checkpoint(task, cp)
        mark_failed(task, e.message)
        return task

    if rec2.status == "denied":
        apply_transition(cp, AgentRuntimeState.FAILED)
        cp.failure = rec2.error or "execute_denied"
        persist_checkpoint(task, cp)
        return task

    # OBSERVING — structured result only
    apply_transition(cp, AgentRuntimeState.OBSERVING)
    obs = {
        "capability_id": cid,
        "status": rec2.status,
        "operation_id": rec2.operation_id,
        "job_id": rec2.job_id,
        "evidence_refs": list(rec2.evidence_refs or [])[:10],
        "result": rec2.result if not reject_fabricated_evidence(rec2.result) else {"fabricated_ignored": True},
    }
    obs_s = json.dumps(obs, default=str)
    if len(obs_s) > MAX_OBSERVATION_CHARS:
        obs = {"capability_id": cid, "status": rec2.status, "truncated": True}
    cp.last_observation = obs
    if rec2.operation_id:
        cp.operation_id = rec2.operation_id
    if rec2.job_id:
        cp.job_id = rec2.job_id
    if rec2.evidence_refs:
        cp.evidence_refs.extend(rec2.evidence_refs)
    # Synthetic evidence ref when substrate does not emit one (deterministic meta caps)
    if not rec2.evidence_refs and rec2.status == "executed":
        syn = f"ev:{task.task_id}:{cp.turn}:{cid}"
        cp.evidence_refs.append(syn)
        obs = dict(obs)
        obs["evidence_refs"] = list(obs.get("evidence_refs") or []) + [syn]
        cp.last_observation = obs
    task.recovery = dict(task.recovery or {})
    hist = list(task.recovery.get("observation_history") or [])
    hist.append(dict(cp.last_observation or {}))
    task.recovery["observation_history"] = hist[-20:]
    # Clear pending after successful observe
    cp.pending_request = None
    persist_checkpoint(task, cp)

    # CHECKPOINTING → PLANNING (next turn) or leave for worker resume
    apply_transition(cp, AgentRuntimeState.CHECKPOINTING)
    persist_checkpoint(task, cp)
    return task


async def run_agent_until_terminal(
    task: GovernedAgentTask,
    *,
    planner: Optional[PlannerFn] = None,
    max_loops: Optional[int] = None,
) -> GovernedAgentTask:
    """Drive turns until terminal/blocked/unknown. Bounded by max_turns."""
    loops = 0
    limit = max_loops if max_loops is not None else max_turns() + 2
    while loops < limit:
        loops += 1
        cp = checkpoint_from_task(task)
        if cp.state in TERMINAL_RUNTIME or cp.state in (
            AgentRuntimeState.BLOCKED, AgentRuntimeState.UNKNOWN, AgentRuntimeState.EXECUTING,
        ):
            # EXECUTING without completion callback stops (async boundary)
            if cp.state == AgentRuntimeState.EXECUTING and cp.last_observation:
                # already observed mid-turn
                pass
            elif cp.state == AgentRuntimeState.EXECUTING:
                break
            else:
                break
        if cp.state == AgentRuntimeState.CHECKPOINTING:
            apply_transition(cp, AgentRuntimeState.PLANNING)
            persist_checkpoint(task, cp)
        task = await run_agent_turn(task, planner=planner, execute_capability=True)
        cp = checkpoint_from_task(task)
        if cp.state == AgentRuntimeState.CHECKPOINTING:
            apply_transition(cp, AgentRuntimeState.PLANNING)
            persist_checkpoint(task, cp)
            continue
    return task


def observe_operation_result(
    task: GovernedAgentTask,
    *,
    operation_id: str,
    status: str,
    evidence_refs: Optional[list] = None,
    result: Optional[dict] = None,
) -> GovernedAgentTask:
    """Resume after async consequential job completes (worker callback)."""
    cp = checkpoint_from_task(task)
    if cp.state == AgentRuntimeState.UNKNOWN:
        return task
    if cp.state != AgentRuntimeState.EXECUTING:
        return task
    st = (status or "").lower()
    if st in ("unknown", "lost"):
        apply_transition(cp, AgentRuntimeState.UNKNOWN)
        cp.unknown_info = {"operation_id": operation_id, "auto_retry": False}
        persist_checkpoint(task, cp)
        mark_unknown(task, reason="operation_unknown")
        return task
    apply_transition(cp, AgentRuntimeState.OBSERVING)
    obs = {
        "operation_id": operation_id,
        "status": st,
        "evidence_refs": list(evidence_refs or [])[:10],
        "result": result if not reject_fabricated_evidence(result) else {"fabricated_ignored": True},
    }
    cp.last_observation = obs
    cp.operation_id = operation_id
    if evidence_refs:
        cp.evidence_refs.extend(evidence_refs)
    persist_checkpoint(task, cp)
    apply_transition(cp, AgentRuntimeState.CHECKPOINTING)
    persist_checkpoint(task, cp)
    return task


def reconcile_unknown(
    task: GovernedAgentTask,
    *,
    resolved_status: str,
    evidence_refs: Optional[list] = None,
) -> GovernedAgentTask:
    """Only after authoritative reconciliation — UNKNOWN → OBSERVING or BLOCKED."""
    cp = checkpoint_from_task(task)
    if cp.state != AgentRuntimeState.UNKNOWN:
        return task
    st = (resolved_status or "").lower()
    if st in ("succeeded", "failed", "cancelled"):
        apply_transition(cp, AgentRuntimeState.OBSERVING)
        cp.last_observation = {
            "reconciled": True,
            "status": st,
            "evidence_refs": list(evidence_refs or []),
        }
        cp.unknown_info = None
        persist_checkpoint(task, cp)
        return task
    apply_transition(cp, AgentRuntimeState.BLOCKED)
    persist_checkpoint(task, cp)
    return task
