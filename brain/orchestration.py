"""
Nuha Planning + Action Orchestration.

Nuha thinks and plans. UCIP authorizes. Existing Agent Runtime executes.
Verification decides success. XP records experience — never authority.

Do NOT treat this module as a second execution engine.
"""
from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.orchestration")

from brain.orchestration_dag import (
    OrchestrationNode,
    OrchestrationEdge,
    NodeStatus,
    DepCondition,
    validate_dag,
    assert_valid_dag,
    compute_readiness,
    propagate_failure,
    check_node_invariants,
    nodes_from_steps,
    DAGValidationError,
)
from brain.specialty_policy import evaluate_node_request, get_specialty_policy
from brain.orchestration_store import persist_plan, load_plan as load_plan_row
from brain.orchestration_verify import verify_workspace_artifacts


# ─── Modes ───────────────────────────────────────────────────────────────────

class NuhaMode(str, enum.Enum):
    CHAT = "chat"
    PLAN = "plan"
    ACTION = "action"


# ─── Orchestration lifecycle (typed transitions) ─────────────────────────────

class OrchStatus(str, enum.Enum):
    IDLE = "idle"
    INTENT_DETECTED = "intent_detected"
    RESPONDING = "responding"
    PLANNING = "planning"
    CONTEXT_GATHERING = "context_gathering"
    GOAL_ANALYSIS = "goal_analysis"
    TASK_DECOMPOSITION = "task_decomposition"
    PERSONA_SELECTION = "persona_selection"
    CAPABILITY_ANALYSIS = "capability_analysis"
    DEPENDENCY_ANALYSIS = "dependency_analysis"
    RISK_ANALYSIS = "risk_analysis"
    VERIFICATION_DESIGN = "verification_design"
    PLAN_READY = "plan_ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    WAITING_FOR_USER = "waiting_for_user"
    ACTION_REQUESTED = "action_requested"
    AUTHORIZATION_PENDING = "authorization_pending"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    AUTHORIZED = "authorized"
    DENIED = "denied"
    BLOCKED = "blocked"
    DELEGATING = "delegating"
    DELEGATED = "delegated"
    QUEUED = "queued"
    RUNNING = "running"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"
    REPLANNING = "replanning"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


# Allowed transitions (from → set of to)
_TRANSITIONS: dict[OrchStatus, set[OrchStatus]] = {
    OrchStatus.IDLE: {OrchStatus.INTENT_DETECTED},
    OrchStatus.INTENT_DETECTED: {
        OrchStatus.RESPONDING, OrchStatus.PLANNING, OrchStatus.ACTION_REQUESTED,
    },
    OrchStatus.RESPONDING: {OrchStatus.COMPLETED},
    OrchStatus.PLANNING: {
        OrchStatus.CONTEXT_GATHERING, OrchStatus.NEEDS_CLARIFICATION, OrchStatus.GOAL_ANALYSIS,
    },
    OrchStatus.CONTEXT_GATHERING: {OrchStatus.GOAL_ANALYSIS},
    OrchStatus.GOAL_ANALYSIS: {OrchStatus.TASK_DECOMPOSITION},
    OrchStatus.TASK_DECOMPOSITION: {OrchStatus.PERSONA_SELECTION},
    OrchStatus.PERSONA_SELECTION: {OrchStatus.CAPABILITY_ANALYSIS},
    OrchStatus.CAPABILITY_ANALYSIS: {OrchStatus.DEPENDENCY_ANALYSIS},
    OrchStatus.DEPENDENCY_ANALYSIS: {OrchStatus.RISK_ANALYSIS},
    OrchStatus.RISK_ANALYSIS: {OrchStatus.VERIFICATION_DESIGN},
    OrchStatus.VERIFICATION_DESIGN: {OrchStatus.PLAN_READY},
    OrchStatus.NEEDS_CLARIFICATION: {OrchStatus.WAITING_FOR_USER},
    OrchStatus.WAITING_FOR_USER: {OrchStatus.PLANNING},
    OrchStatus.PLAN_READY: {OrchStatus.ACTION_REQUESTED, OrchStatus.COMPLETED},
    OrchStatus.ACTION_REQUESTED: {OrchStatus.AUTHORIZATION_PENDING, OrchStatus.PLANNING},
    OrchStatus.AUTHORIZATION_PENDING: {
        OrchStatus.DENIED, OrchStatus.AWAITING_APPROVAL, OrchStatus.APPROVED, OrchStatus.AUTHORIZED,
    },
    OrchStatus.AWAITING_APPROVAL: {OrchStatus.AUTHORIZATION_PENDING, OrchStatus.DENIED, OrchStatus.CANCELLED},
    OrchStatus.APPROVED: {OrchStatus.AUTHORIZED},
    OrchStatus.AUTHORIZED: {OrchStatus.DELEGATING},
    OrchStatus.DENIED: {OrchStatus.BLOCKED},
    OrchStatus.BLOCKED: set(),
    OrchStatus.DELEGATING: {OrchStatus.DELEGATED, OrchStatus.FAILED},
    OrchStatus.DELEGATED: {OrchStatus.QUEUED},
    OrchStatus.QUEUED: {OrchStatus.RUNNING, OrchStatus.CANCELLATION_REQUESTED},
    OrchStatus.RUNNING: {
        OrchStatus.VERIFYING, OrchStatus.FAILED, OrchStatus.CANCELLATION_REQUESTED,
    },
    OrchStatus.VERIFYING: {
        OrchStatus.VERIFIED, OrchStatus.VERIFICATION_FAILED, OrchStatus.CANCELLATION_REQUESTED,
    },
    OrchStatus.VERIFIED: {OrchStatus.COMPLETING},
    OrchStatus.VERIFICATION_FAILED: {OrchStatus.REPLANNING, OrchStatus.FAILED},
    OrchStatus.COMPLETING: {OrchStatus.COMPLETED},
    OrchStatus.COMPLETED: set(),
    OrchStatus.FAILED: {OrchStatus.RECOVERING, OrchStatus.CANCELLED},
    OrchStatus.RECOVERING: {OrchStatus.REPLANNING, OrchStatus.FAILED},
    OrchStatus.REPLANNING: {OrchStatus.PLAN_READY, OrchStatus.PLANNING},
    OrchStatus.CANCELLATION_REQUESTED: {OrchStatus.CANCELLING},
    OrchStatus.CANCELLING: {OrchStatus.CANCELLED},
    OrchStatus.CANCELLED: set(),
}


def can_transition(frm: OrchStatus, to: OrchStatus) -> bool:
    return to in _TRANSITIONS.get(frm, set())


def transition(frm: OrchStatus, to: OrchStatus) -> OrchStatus:
    if not can_transition(frm, to):
        raise ValueError(f"invalid orchestration transition: {frm.value} → {to.value}")
    return to


TERMINAL = {
    OrchStatus.COMPLETED,
    OrchStatus.FAILED,
    OrchStatus.CANCELLED,
    OrchStatus.BLOCKED,
}


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class OrchestrationStep:
    id: str
    description: str
    persona_id: str = "code"
    dependencies: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    workspace_scope: str = "project"
    expected_output: str = ""
    verification_criteria: list[str] = field(default_factory=list)
    status: str = "pending"
    job_or_task_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "persona_id": self.persona_id,
            "dependencies": list(self.dependencies),
            "required_capabilities": list(self.required_capabilities),
            "workspace_scope": self.workspace_scope,
            "expected_output": self.expected_output,
            "verification_criteria": list(self.verification_criteria),
            "status": self.status,
            "job_or_task_id": self.job_or_task_id,
        }


@dataclass
class OrchestrationPlan:
    id: str
    goal: str
    mode: str = NuhaMode.PLAN.value
    intent: str = "creation"
    workspace_id: str = "default"
    user_id: str = ""
    status: str = OrchStatus.IDLE.value
    assumptions: list[str] = field(default_factory=list)
    steps: list[OrchestrationStep] = field(default_factory=list)  # legacy view
    nodes: list = field(default_factory=list)  # OrchestrationNode
    edges: list = field(default_factory=list)  # OrchestrationEdge
    personas: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risk_level: str = RiskLevel.LOW.value
    requires_hitl: bool = False
    verification_plan: list[str] = field(default_factory=list)
    expected_artifacts: list[str] = field(default_factory=list)
    estimated_budget: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    agent_task_ids: list[str] = field(default_factory=list)
    dag_valid: bool = True
    dag_issues: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "plan_id": self.id,
            "goal": self.goal,
            "mode": self.mode,
            "intent": self.intent,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "status": self.status,
            "assumptions": list(self.assumptions),
            "steps": [s.to_dict() for s in self.steps],
            "nodes": [n.to_dict() if hasattr(n, "to_dict") else n for n in self.nodes],
            "edges": [e.to_dict() if hasattr(e, "to_dict") else e for e in self.edges],
            "dag_valid": self.dag_valid,
            "dag_issues": list(self.dag_issues),
            "personas": list(self.personas),
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
            "risk_level": self.risk_level,
            "requires_hitl": self.requires_hitl,
            "verification_plan": list(self.verification_plan),
            "expected_artifacts": list(self.expected_artifacts),
            "estimated_budget": dict(self.estimated_budget),
            "events": list(self.events[-50:]),
            "agent_task_ids": list(self.agent_task_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "authority_note": (
                "Identifying capabilities in a plan does not grant them. "
                "UCIP authorizes execution. XP does not grant authority."
            ),
        }

    def set_status(self, new: OrchStatus) -> None:
        cur = OrchStatus(self.status)
        self.status = transition(cur, new).value
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.emit(f"status.{self.status}", {"from": cur.value, "to": self.status})

    def emit(self, event_type: str, data: Optional[dict] = None) -> None:
        self.events.append({
            "type": event_type,
            "data": data or {},
            "at": datetime.now(timezone.utc).isoformat(),
        })


# In-memory store (per process). Durable extension can persist later.
_PLANS: dict[str, OrchestrationPlan] = {}


def get_plan(plan_id: str) -> Optional[OrchestrationPlan]:
    return _PLANS.get(plan_id)


async def get_plan_durable(plan_id: str) -> Optional[OrchestrationPlan]:
    """Memory first, then durable store. Reconstructs plan object for recovery."""
    if plan_id in _PLANS:
        return _PLANS[plan_id]
    data = await load_plan_row(plan_id)
    if not data:
        return None
    plan = _plan_from_dict(data)
    _PLANS[plan.id] = plan
    return plan


def _plan_from_dict(data: dict) -> OrchestrationPlan:
    from brain.orchestration_dag import OrchestrationNode, OrchestrationEdge
    plan = OrchestrationPlan(
        id=data.get("id") or data.get("plan_id"),
        goal=data.get("goal") or "",
        mode=data.get("mode") or NuhaMode.PLAN.value,
        intent=data.get("intent") or "creation",
        workspace_id=data.get("workspace_id") or "default",
        user_id=data.get("user_id") or "",
        status=data.get("status") or OrchStatus.IDLE.value,
        assumptions=list(data.get("assumptions") or []),
        personas=list(data.get("personas") or []),
        capabilities=list(data.get("capabilities") or []),
        dependencies=list(data.get("dependencies") or []),
        risk_level=data.get("risk_level") or RiskLevel.LOW.value,
        requires_hitl=bool(data.get("requires_hitl")),
        verification_plan=list(data.get("verification_plan") or []),
        expected_artifacts=list(data.get("expected_artifacts") or []),
        estimated_budget=dict(data.get("estimated_budget") or {}),
        events=list(data.get("events") or []),
        agent_task_ids=list(data.get("agent_task_ids") or []),
        dag_valid=bool(data.get("dag_valid", True)),
        dag_issues=list(data.get("dag_issues") or []),
    )
    for nd in data.get("nodes") or []:
        plan.nodes.append(OrchestrationNode(
            id=nd["id"],
            description=nd.get("description") or "",
            persona_id=nd.get("persona_id") or "code",
            dependencies=list(nd.get("dependencies") or []),
            capabilities=list(nd.get("capabilities") or []),
            workspace_scope=nd.get("workspace_scope") or "project",
            expected_outputs=list(nd.get("expected_outputs") or []),
            verification_criteria=list(nd.get("verification_criteria") or []),
            risk=nd.get("risk") or "low",
            status=nd.get("status") or "pending",
            job_or_task_id=nd.get("job_or_task_id"),
            authorization_decision=nd.get("authorization_decision"),
            blocking_reason=nd.get("blocking_reason"),
            verification_evidence=nd.get("verification_evidence"),
        ))
    for ed in data.get("edges") or []:
        plan.edges.append(OrchestrationEdge(
            source=ed["source"], target=ed["target"],
            condition=ed.get("condition") or "verified",
        ))
    # rebuild steps for sequential runner compatibility
    for n in plan.nodes:
        plan.steps.append(OrchestrationStep(
            id=n.id,
            description=n.description,
            persona_id=n.persona_id,
            dependencies=list(n.dependencies),
            required_capabilities=list(n.capabilities),
            expected_output=(n.expected_outputs or [""])[0] if n.expected_outputs else "",
            verification_criteria=list(n.verification_criteria),
            status=n.status,
            job_or_task_id=n.job_or_task_id,
        ))
    return plan


def list_plans_for_user(user_id: str, limit: int = 20) -> list[OrchestrationPlan]:
    items = [p for p in _PLANS.values() if p.user_id == user_id]
    items.sort(key=lambda p: p.updated_at, reverse=True)
    return items[:limit]


def detect_mode(goal: str, explicit: Optional[str] = None) -> NuhaMode:
    if explicit:
        try:
            return NuhaMode(explicit.lower())
        except ValueError:
            pass
    g = (goal or "").lower().strip()
    plan_cues = (
        "plan ", "plan a", "plan the", "create a plan", "implementation plan",
        "what would you need", "how would you", "design a strategy", "outline steps",
    )
    action_cues = (
        "build it", "execute", "implement", "create the", "fix the",
        "do it", "run the plan", "make me", "scaffold", "deploy",
    )
    if any(g.startswith(c) or c in g for c in plan_cues):
        return NuhaMode.PLAN
    if any(c in g for c in action_cues):
        return NuhaMode.ACTION
    return NuhaMode.CHAT


def _pick_persona(description: str) -> str:
    from brain.personas import suggest_personas_for_goal
    suggested = suggest_personas_for_goal(description)
    return suggested[0] if suggested else "code"


def _caps_for_persona(persona_id: str, description: str = "") -> list[str]:
    caps = ["fs.read"]
    d = (description or "").lower()
    if any(k in d for k in ("write", "create", "implement", "build", "edit", "fix")):
        caps.append("fs.write")
    if any(k in d for k in ("run", "build", "install", "test", "shell", "npm", "pip")):
        caps.append("shell.exec")
    if "research" in d or persona_id == "research":
        caps.append("web.search")
    if persona_id == "automation" or "workflow" in d:
        caps.append("workflow.write")
    return caps


def _heuristic_steps(goal: str) -> list[OrchestrationStep]:
    g = goal.lower()
    steps: list[OrchestrationStep] = []
    if any(k in g for k in ("website", "page", "frontend", "landing", "ui", "dashboard")):
        steps.append(OrchestrationStep(
            id="s1",
            description="Clarify visual structure and content sections for the page",
            persona_id="design",
            required_capabilities=["fs.read"],
            expected_output="Design brief / section outline",
            verification_criteria=["Outline covers requested content"],
        ))
        steps.append(OrchestrationStep(
            id="s2",
            description="Implement a one-page site with HTML/CSS (or project stack) matching the brief",
            persona_id="web",
            dependencies=["s1"],
            required_capabilities=["fs.read", "fs.write"],
            expected_output="Website files in workspace",
            verification_criteria=["Index/entry file exists", "Content matches goal"],
        ))
        steps.append(OrchestrationStep(
            id="s3",
            description="Validate structure and fix obvious issues",
            persona_id="code",
            dependencies=["s2"],
            required_capabilities=["fs.read", "fs.write", "shell.exec"],
            expected_output="Validated files",
            verification_criteria=["No critical syntax errors if build available"],
        ))
    elif any(k in g for k in ("workflow", "automat", "trigger")):
        steps.append(OrchestrationStep(
            id="s1",
            description="Design workflow nodes and triggers",
            persona_id="automation",
            required_capabilities=["fs.read", "workflow.write"],
            expected_output="Workflow definition",
            verification_criteria=["Workflow graph is valid"],
        ))
    elif any(k in g for k in ("research", "look up", "investigate")):
        steps.append(OrchestrationStep(
            id="s1",
            description="Research and synthesize findings",
            persona_id="research",
            required_capabilities=["web.search", "fs.write"],
            expected_output="Research brief",
            verification_criteria=["Sources or findings recorded"],
        ))
    else:
        steps.append(OrchestrationStep(
            id="s1",
            description=f"Analyze and implement: {goal[:200]}",
            persona_id=_pick_persona(goal),
            required_capabilities=_caps_for_persona(_pick_persona(goal), goal),
            expected_output="Completed work product",
            verification_criteria=["Primary goal addressed in workspace"],
        ))
        if any(k in g for k in ("test", "verify", "fix")):
            steps.append(OrchestrationStep(
                id="s2",
                description="Run checks and fix failures",
                persona_id="code",
                dependencies=["s1"],
                required_capabilities=["fs.read", "shell.exec", "fs.write"],
                expected_output="Passing checks or documented gaps",
                verification_criteria=["Checks executed"],
            ))
    # Fill caps if empty
    for s in steps:
        if not s.required_capabilities:
            s.required_capabilities = _caps_for_persona(s.persona_id, s.description)
    return steps


def _risk_for_caps(caps: list[str], goal: str) -> tuple[str, bool]:
    g = goal.lower()
    high = any(k in g for k in ("delete", "production", "drop database", "rm -rf", "deploy prod"))
    if high or "fs.delete" in caps:
        return RiskLevel.CRITICAL.value, True
    if "shell.exec" in caps:
        return RiskLevel.MEDIUM.value, False
    if "fs.write" in caps:
        return RiskLevel.LOW.value, False
    return RiskLevel.LOW.value, False


async def create_plan(
    *,
    user_id: str,
    goal: str,
    workspace_id: str = "default",
    mode: Optional[str] = None,
    persona_id: str = "nuha",
) -> OrchestrationPlan:
    """Run planning state machine to PLAN_READY. Read-only; no writes."""
    plan_id = str(uuid.uuid4())
    plan = OrchestrationPlan(
        id=plan_id,
        goal=goal,
        mode=NuhaMode.PLAN.value,
        workspace_id=workspace_id or "default",
        user_id=user_id,
        status=OrchStatus.IDLE.value,
    )
    _PLANS[plan_id] = plan

    plan.set_status(OrchStatus.INTENT_DETECTED)
    plan.emit("intent.detected", {"goal": goal, "persona_id": persona_id})
    plan.set_status(OrchStatus.PLANNING)
    plan.emit("planning.started", {})

    # Context gathering (permitted reads — best-effort, no writes)
    plan.set_status(OrchStatus.CONTEXT_GATHERING)
    context: dict[str, Any] = {"workspace_id": workspace_id}
    try:
        from execution.files import FileService
        fs = FileService(user_id, workspace_id or "default")
        # Lightweight listing if available
        listing = []
        try:
            if hasattr(fs, "list_dir"):
                listing = await fs.list_dir(".") if hasattr(fs.list_dir, "__await__") else fs.list_dir(".")
            elif hasattr(fs, "list"):
                listing = fs.list(".")
        except Exception:
            listing = []
        context["files_sample"] = (listing or [])[:30]
        plan.emit("context.gathered", {"files_sample_count": len(context["files_sample"])})
    except Exception as e:
        plan.emit("context.gathered", {"warning": str(e)[:200]})

    plan.set_status(OrchStatus.GOAL_ANALYSIS)
    try:
        from cognitive.intent import IntentParser
        intent = await IntentParser().parse(goal)
        plan.intent = intent.goal_type
        plan.assumptions = list(intent.constraints or [])
        plan.estimated_budget = {
            "complexity": intent.complexity,
            "estimated_steps": intent.estimated_steps,
        }
        if intent.expected_outcome:
            plan.expected_artifacts.append(intent.expected_outcome)
    except Exception:
        from brain.personas import classify_intent_heuristic
        classes = classify_intent_heuristic(goal)
        plan.intent = classes[0].lower() if classes else "other"
        plan.assumptions = ["Heuristic intent parse (LLM intent unavailable)"]

    plan.set_status(OrchStatus.TASK_DECOMPOSITION)
    steps = _heuristic_steps(goal)
    try:
        from cognitive.decomposer import GoalDecomposer
        from brain.llm import BrainLLM
        # Optional LLM refine — if fails, keep heuristic
        # Skip heavy LLM if not configured; heuristics remain valid plans
        _ = GoalDecomposer  # reserved for future refine path
    except Exception:
        pass
    plan.steps = steps
    plan.nodes, plan.edges = nodes_from_steps(steps)
    plan.dag_issues = validate_dag(plan.nodes, plan.edges)
    plan.dag_valid = len(plan.dag_issues) == 0
    if not plan.dag_valid:
        plan.emit("plan.dag_invalid", {"issues": plan.dag_issues})
        # Still surface plan but mark blocked-ready path
    plan.emit("plan.dag", {
        "nodes": len(plan.nodes),
        "edges": len(plan.edges),
        "valid": plan.dag_valid,
    })

    plan.set_status(OrchStatus.PERSONA_SELECTION)
    plan.personas = list(dict.fromkeys([s.persona_id for s in plan.steps] + ["nuha"]))
    plan.emit("plan.personas", {"personas": plan.personas})

    plan.set_status(OrchStatus.CAPABILITY_ANALYSIS)
    caps: list[str] = []
    for s in plan.steps:
        caps.extend(s.required_capabilities)
    plan.capabilities = list(dict.fromkeys(caps))
    plan.emit("plan.capabilities", {
        "capabilities": plan.capabilities,
        "note": "Listed capabilities are requirements, not grants. UCIP authorizes at execution.",
    })

    plan.set_status(OrchStatus.DEPENDENCY_ANALYSIS)
    deps = []
    for s in plan.steps:
        for d in s.dependencies:
            deps.append(f"{d}->{s.id}")
    plan.dependencies = deps

    plan.set_status(OrchStatus.RISK_ANALYSIS)
    risk, hitl = _risk_for_caps(plan.capabilities, goal)
    plan.risk_level = risk
    plan.requires_hitl = hitl

    plan.set_status(OrchStatus.VERIFICATION_DESIGN)
    vplan = []
    for s in plan.steps:
        vplan.extend(s.verification_criteria)
    if not vplan:
        vplan = ["Primary goal reflected in workspace artifacts"]
    plan.verification_plan = list(dict.fromkeys(vplan))
    if not plan.expected_artifacts:
        plan.expected_artifacts = [s.expected_output for s in plan.steps if s.expected_output]

    plan.set_status(OrchStatus.PLAN_READY)
    plan.emit("plan.created", {"plan_id": plan.id, "steps": len(plan.steps)})
    await persist_plan(plan)
    return plan


async def authorize_plan_execution(plan: OrchestrationPlan) -> tuple[bool, str]:
    """UCIP gate — identifying caps in plan does not grant them."""
    plan.set_status(OrchStatus.AUTHORIZATION_PENDING)
    plan.emit("authorization.requested", {"capabilities": plan.capabilities})

    if plan.requires_hitl and plan.risk_level == RiskLevel.CRITICAL.value:
        plan.set_status(OrchStatus.AWAITING_APPROVAL)
        plan.emit("hitl.requested", {"reason": "critical risk operation"})
        # For now, critical without explicit approval token stays pending/blocked path
        plan.set_status(OrchStatus.DENIED)
        plan.emit("authorization.denied", {"reason": "HITL required for critical risk"})
        plan.set_status(OrchStatus.BLOCKED)
        return False, "HITL required for critical risk — not auto-approved"

    try:
        from governance.ucip import AgentIdentity, TrustLevel
        identity = AgentIdentity.create(
            user_id=plan.user_id,
            session_id=f"orch:{plan.id}",
            trust_level=TrustLevel.OPERATOR,
            extra_caps=set(plan.capabilities) if plan.capabilities else None,
        )
        _ = identity
        plan.set_status(OrchStatus.AUTHORIZED)
        plan.emit("authorization.granted", {
            "note": "Plan authorized to proceed to existing agent runtime; each tool still UCIP-gated",
            "agent_id": identity.agent_id,
        })
        return True, "authorized"
    except Exception as e:
        # If UCIP import shape differs, fail closed on critical only
        if plan.risk_level == RiskLevel.CRITICAL.value:
            plan.set_status(OrchStatus.DENIED)
            plan.emit("authorization.denied", {"reason": str(e)[:200]})
            plan.set_status(OrchStatus.BLOCKED)
            return False, str(e)
        plan.set_status(OrchStatus.AUTHORIZED)
        plan.emit("authorization.granted", {"note": f"soft-path: {e}"[:200]})
        return True, "authorized_soft"


async def execute_plan(plan: OrchestrationPlan) -> OrchestrationPlan:
    """Action mode: validate DAG → specialty policy → UCIP path → AgentRuntime."""
    if OrchStatus(plan.status) == OrchStatus.PLAN_READY:
        plan.set_status(OrchStatus.ACTION_REQUESTED)
    elif OrchStatus(plan.status) not in (
        OrchStatus.ACTION_REQUESTED, OrchStatus.AUTHORIZED, OrchStatus.PLAN_READY,
    ):
        if OrchStatus(plan.status) in TERMINAL:
            raise ValueError(f"plan is terminal: {plan.status}")

    # Ensure DAG present
    if not plan.nodes and plan.steps:
        plan.nodes, plan.edges = nodes_from_steps(plan.steps)
    plan.dag_issues = validate_dag(plan.nodes, plan.edges)
    plan.dag_valid = len(plan.dag_issues) == 0
    if not plan.dag_valid:
        plan.emit("plan.dag_invalid", {"issues": plan.dag_issues})
        plan.status = OrchStatus.BLOCKED.value
        plan.emit("status.blocked", {"reason": "PLAN_INVALID", "issues": plan.dag_issues})
        return plan

    ok, reason = await authorize_plan_execution(plan)
    if not ok:
        await persist_plan(plan)
        return plan

    plan.set_status(OrchStatus.DELEGATING)
    plan.emit("delegation.created", {"steps": [s.id for s in plan.steps]})

    from brain.agent_runtime import AgentRuntime, AgentContext
    from brain.agent_tools import AgentMode

    # Sequential dependency-aware execution
    done: set[str] = set()
    plan.set_status(OrchStatus.DELEGATED)
    plan.set_status(OrchStatus.QUEUED)

    remaining = {s.id: s for s in plan.steps}
    plan.set_status(OrchStatus.RUNNING)

    while remaining:
        # DAG readiness (nodes) — sequential pick among ready
        ready_ids = compute_readiness(plan.nodes, plan.edges)
        plan.emit("dag.readiness", {"ready": ready_ids})
        if OrchStatus(plan.status) == OrchStatus.CANCELLATION_REQUESTED:
            plan.set_status(OrchStatus.CANCELLING)
            plan.set_status(OrchStatus.CANCELLED)
            return plan

        ready = [
            s for s in remaining.values()
            if all(d in done for d in s.dependencies)
        ]
        if not ready:
            plan.set_status(OrchStatus.FAILED)
            plan.emit("job.failed", {"reason": "unresolvable step dependencies"})
            return plan

        step = ready[0]
        step.status = "running"
        # Specialty policy (least privilege) — still not a second auth engine
        node = next((n for n in plan.nodes if n.id == step.id), None)
        if node:
            try:
                node.set_status(NodeStatus.AUTHORIZATION_PENDING)
            except Exception:
                node.status = NodeStatus.AUTHORIZATION_PENDING.value
            decision = evaluate_node_request(
                persona_id=step.persona_id,
                requested_caps=set(step.required_capabilities or node.capabilities or []),
                risk=plan.risk_level,
            )
            plan.emit("ucip.policy_eval", {
                "node_id": step.id,
                "persona_id": step.persona_id,
                "decision": decision.to_dict(),
            })
            if not decision.allow:
                node.status = NodeStatus.BLOCKED.value
                node.blocking_reason = "; ".join(decision.reasons)
                node.authorization_decision = "deny"
                step.status = "failed"
                plan.status = OrchStatus.BLOCKED.value
                plan.emit("authorization.denied", {"node_id": step.id, "reasons": decision.reasons})
                return plan
            if decision.hitl_required and plan.risk_level == RiskLevel.CRITICAL.value:
                node.status = NodeStatus.AWAITING_APPROVAL.value
                plan.set_status(OrchStatus.AWAITING_APPROVAL)
                plan.emit("hitl.requested", {"node_id": step.id})
                plan.set_status(OrchStatus.DENIED)
                plan.set_status(OrchStatus.BLOCKED)
                node.status = NodeStatus.BLOCKED.value
                node.blocking_reason = "HITL required"
                return plan
            node.authorization_decision = "allow"
            try:
                node.set_status(NodeStatus.AUTHORIZED)
                node.set_status(NodeStatus.QUEUED)
                node.set_status(NodeStatus.RUNNING)
            except Exception:
                node.status = NodeStatus.RUNNING.value
            # Narrow step caps to effective
            step.required_capabilities = list(decision.effective_caps) or step.required_capabilities

        plan.emit("job.started", {"step_id": step.id, "persona_id": step.persona_id})

        objective = (
            f"[Nuha orchestration step {step.id} | persona={step.persona_id}]\n"
            f"Goal: {plan.goal}\n"
            f"Step: {step.description}\n"
            f"Expected: {step.expected_output}\n"
            f"Verify: {', '.join(step.verification_criteria)}"
        )
        runtime = AgentRuntime(
            user_id=plan.user_id,
            project_id=plan.workspace_id or "default",
            tenant_id=None,
            mode=AgentMode.AGENT,
        )
        context = AgentContext(
            project_id=plan.workspace_id or "default",
            user_request=objective,
        )
        success = False
        files_changed: list = []
        try:
            async for event in runtime.run(objective, context):
                et = (event or {}).get("type")
                data = (event or {}).get("data") or {}
                tid = (event or {}).get("task_id")
                if tid and tid not in plan.agent_task_ids:
                    plan.agent_task_ids.append(tid)
                    step.job_or_task_id = tid
                if et == "agent.completed":
                    success = data.get("success") is not False
                    files_changed = data.get("files_changed") or []
                if et in ("agent.cancelled",):
                    plan.set_status(OrchStatus.CANCELLATION_REQUESTED)
        except Exception as e:
            plan.emit("job.failed", {"step_id": step.id, "error": str(e)[:300]})
            success = False

        if OrchStatus(plan.status) == OrchStatus.CANCELLATION_REQUESTED:
            plan.set_status(OrchStatus.CANCELLING)
            plan.set_status(OrchStatus.CANCELLED)
            return plan

        if not success:
            step.status = "failed"
            node = next((n for n in plan.nodes if n.id == step.id), None)
            if node:
                try:
                    node.set_status(NodeStatus.FAILED)
                except Exception:
                    node.status = NodeStatus.FAILED.value
                propagate_failure(plan.nodes, plan.edges, step.id)
            plan.set_status(OrchStatus.FAILED)
            plan.emit("job.failed", {"step_id": step.id})
            # Recovery path: replan once
            plan.set_status(OrchStatus.RECOVERING)
            plan.emit("recovery.started", {"step_id": step.id})
            plan.set_status(OrchStatus.REPLANNING)
            # Simple recovery: retry same step once marked pending
            step.status = "pending"
            plan.set_status(OrchStatus.PLAN_READY)
            plan.set_status(OrchStatus.ACTION_REQUESTED)
            # Retry once inline
            ok2, _ = await authorize_plan_execution(plan)
            if not ok2:
                return plan
            plan.set_status(OrchStatus.DELEGATING)
            plan.set_status(OrchStatus.DELEGATED)
            plan.set_status(OrchStatus.QUEUED)
            plan.set_status(OrchStatus.RUNNING)
            step.status = "running"
            try:
                runtime2 = AgentRuntime(
                    user_id=plan.user_id,
                    project_id=plan.workspace_id or "default",
                    mode=AgentMode.AGENT,
                )
                async for event in runtime2.run(objective + "\n(Retry after failure)", context):
                    et = (event or {}).get("type")
                    data = (event or {}).get("data") or {}
                    if et == "agent.completed":
                        success = data.get("success") is not False
                        files_changed = data.get("files_changed") or []
            except Exception as e2:
                plan.emit("job.failed", {"step_id": step.id, "error": str(e2)[:300], "retry": True})
                success = False
            if not success:
                step.status = "failed"
                plan.set_status(OrchStatus.FAILED)
                return plan

        step.status = "done"
        node = next((n for n in plan.nodes if n.id == step.id), None)
        if node:
            node.job_or_task_id = step.job_or_task_id
            node.verification_evidence = {
                "success": True,
                "files_changed_count": len(files_changed or []),
                "criteria": list(step.verification_criteria or []),
            }
            try:
                node.status = NodeStatus.VERIFYING.value
                node.set_status(NodeStatus.VERIFIED)
                node.set_status(NodeStatus.COMPLETED)
            except Exception:
                node.status = NodeStatus.COMPLETED.value
        done.add(step.id)
        del remaining[step.id]

        # XP — evidence based, never authority
        try:
            from brain.persona_xp import award_agent_task_outcome
            tid = step.job_or_task_id or plan.id
            await award_agent_task_outcome(
                user_id=plan.user_id,
                task_id=str(tid),
                success=True,
                objective=step.description,
                files_changed=files_changed,
                persona_id=step.persona_id,
            )
        except Exception:
            pass

    # Verification — execution success is not completion
    plan.set_status(OrchStatus.VERIFYING)
    plan.emit("verification.started", {"criteria": plan.verification_plan})
    await persist_plan(plan)
    evidence = await verify_workspace_artifacts(
        user_id=plan.user_id,
        workspace_id=plan.workspace_id or "default",
        goal=plan.goal,
        expected_outputs=plan.expected_artifacts,
        files_changed=None,
    )
    plan.emit("verification.checked", evidence)
    verified = bool(evidence.get("passed"))
    for n in plan.nodes:
        if n.status in ("completed", "verified") or NodeStatus(n.status) in (
            NodeStatus.COMPLETED, NodeStatus.VERIFIED
        ):
            n.verification_evidence = evidence

    if verified:
        plan.set_status(OrchStatus.VERIFIED)
        plan.emit("verification.passed", {})
        plan.set_status(OrchStatus.COMPLETING)
        try:
            from brain.persona_xp import award_xp
            from core.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await award_xp(
                    db,
                    user_id=plan.user_id,
                    persona_id="nuha",
                    event_type="orchestration_success",
                    reason=f"Orchestration completed: {plan.goal[:120]}",
                    task_id=plan.id,
                    source="orchestration",
                    verified=True,
                    idempotency_key=f"orch-complete:{plan.id}",
                    specialty_category="orchestration",
                )
                await db.commit()
        except Exception:
            pass
        plan.emit("xp.awarded", {"persona_id": "nuha"})
        plan.set_status(OrchStatus.COMPLETED)
        plan.emit("orchestration.completed", {"plan_id": plan.id})
        await persist_plan(plan)
    else:
        plan.set_status(OrchStatus.VERIFICATION_FAILED)
        plan.emit("verification.failed", evidence)
        plan.set_status(OrchStatus.FAILED)
        await persist_plan(plan)

    return plan


def request_cancel(plan_id: str) -> Optional[OrchestrationPlan]:
    plan = get_plan(plan_id)
    if not plan:
        return None
    st = OrchStatus(plan.status)
    if st in TERMINAL:
        return plan
    # Force into cancellation path if allowed, else mark requested
    if can_transition(st, OrchStatus.CANCELLATION_REQUESTED):
        plan.set_status(OrchStatus.CANCELLATION_REQUESTED)
    else:
        # Jump via running-compatible states if needed
        plan.status = OrchStatus.CANCELLATION_REQUESTED.value
        plan.emit("status.cancellation_requested", {"forced": True})
    # Try agent cancel
    for tid in plan.agent_task_ids:
        try:
            from brain.agent_runtime import request_cancel as agent_cancel
            agent_cancel(tid)
        except Exception:
            pass
    if can_transition(OrchStatus(plan.status), OrchStatus.CANCELLING):
        plan.set_status(OrchStatus.CANCELLING)
    if can_transition(OrchStatus(plan.status), OrchStatus.CANCELLED):
        plan.set_status(OrchStatus.CANCELLED)
    plan.emit("orchestration.cancelled", {"plan_id": plan.id})
    return plan
