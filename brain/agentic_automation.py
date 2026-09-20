"""Governed agentic automation — Nuha orchestrates; agents do not execute freely.

Architecture
------------
Nuha / automation step
        ↓
delegate_agent_task (durable identity + capability bound)
        ↓
UCIP / CapabilitySubstrate authorization
        ↓
ExecutionOperation / ExecutionJob (consequential only)
        ↓
Existing executor + isolation / credentials
        ↓
Evidence (real substrate only)
        ↓
Agent task result (data, not proof)

Agents never self-grant capabilities, never bypass UCIP, never invent evidence.
AgentTask is work/state. ExecutionOperation remains the consequential ledger.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("devos.agentic_automation")


class AgentTaskLifecycle(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    AWAITING_CAPABILITY = "awaiting_capability"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


TERMINAL = frozenset({
    AgentTaskLifecycle.COMPLETED,
    AgentTaskLifecycle.FAILED,
    AgentTaskLifecycle.CANCELLED,
    AgentTaskLifecycle.UNKNOWN,
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return "agt_" + uuid.uuid4().hex[:16]


def agent_task_idempotency_key(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    parent_run_id: Optional[str],
    agent_type: str,
    logical_key: str,
) -> str:
    raw = json.dumps(
        {
            "owner_id": owner_id,
            "tenant_id": tenant_id or "",
            "parent_run_id": parent_run_id or "",
            "agent_type": agent_type,
            "logical_key": logical_key,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class CapabilityGrant:
    """Server-side grant only — never accept client-supplied grants."""
    capability_id: str
    authorized: bool
    reason: str = ""
    source: str = "server"  # must be server


@dataclass
class CapabilityRequestRecord:
    capability_id: str
    inputs: dict = field(default_factory=dict)
    status: str = "requested"  # requested | authorized | denied | executed | unknown
    operation_id: Optional[str] = None
    job_id: Optional[str] = None
    evidence_refs: list = field(default_factory=list)
    error: Optional[str] = None
    result: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "inputs": dict(self.inputs or {}),
            "status": self.status,
            "operation_id": self.operation_id,
            "job_id": self.job_id,
            "evidence_refs": list(self.evidence_refs or []),
            "error": self.error,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityRequestRecord":
        return cls(
            capability_id=str(d.get("capability_id") or ""),
            inputs=dict(d.get("inputs") or {}),
            status=str(d.get("status") or "requested"),
            operation_id=d.get("operation_id"),
            job_id=d.get("job_id"),
            evidence_refs=list(d.get("evidence_refs") or []),
            error=d.get("error"),
            result=d.get("result"),
        )


@dataclass
class GovernedAgentTask:
    """Durable agent work unit for automation. Not an ExecutionOperation."""

    task_id: str
    owner_id: str
    tenant_id: Optional[str]
    agent_type: str
    status: AgentTaskLifecycle = AgentTaskLifecycle.PENDING
    task_input: dict = field(default_factory=dict)
    allowed_capabilities: list[str] = field(default_factory=list)
    capability_requests: list[CapabilityRequestRecord] = field(default_factory=list)
    parent_run_id: Optional[str] = None
    parent_step_id: Optional[str] = None
    parent_operation_id: Optional[str] = None
    workflow_id: Optional[str] = None
    workflow_version: Optional[int] = None
    idempotency_key: Optional[str] = None
    authorization: dict = field(default_factory=dict)
    plan: Optional[dict] = None  # reasoning only — not evidence
    result: dict = field(default_factory=dict)
    evidence_refs: list = field(default_factory=list)
    operation_ids: list = field(default_factory=list)
    job_ids: list = field(default_factory=list)
    error: Optional[str] = None
    recovery: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "owner_id": self.owner_id,
            "tenant_id": self.tenant_id,
            "agent_type": self.agent_type,
            "status": self.status.value if isinstance(self.status, AgentTaskLifecycle) else self.status,
            "task_input": dict(self.task_input or {}),
            "allowed_capabilities": list(self.allowed_capabilities or []),
            "capability_requests": [c.to_dict() for c in self.capability_requests],
            "parent_run_id": self.parent_run_id,
            "parent_step_id": self.parent_step_id,
            "parent_operation_id": self.parent_operation_id,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "idempotency_key": self.idempotency_key,
            "authorization": dict(self.authorization or {}),
            "plan": self.plan,
            "result": dict(self.result or {}),
            "evidence_refs": list(self.evidence_refs or []),
            "operation_ids": list(self.operation_ids or []),
            "job_ids": list(self.job_ids or []),
            "error": self.error,
            "recovery": dict(self.recovery or {}),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GovernedAgentTask":
        st = d.get("status") or AgentTaskLifecycle.PENDING.value
        try:
            status = AgentTaskLifecycle(str(st))
        except Exception:
            status = AgentTaskLifecycle.PENDING
        caps = [
            CapabilityRequestRecord.from_dict(x)
            for x in (d.get("capability_requests") or [])
            if isinstance(x, dict)
        ]
        return cls(
            task_id=str(d.get("task_id") or ""),
            owner_id=str(d.get("owner_id") or ""),
            tenant_id=d.get("tenant_id"),
            agent_type=str(d.get("agent_type") or "worker"),
            status=status,
            task_input=dict(d.get("task_input") or {}),
            allowed_capabilities=list(d.get("allowed_capabilities") or []),
            capability_requests=caps,
            parent_run_id=d.get("parent_run_id"),
            parent_step_id=d.get("parent_step_id"),
            parent_operation_id=d.get("parent_operation_id"),
            workflow_id=d.get("workflow_id"),
            workflow_version=int(d["workflow_version"]) if d.get("workflow_version") is not None else None,
            idempotency_key=d.get("idempotency_key"),
            authorization=dict(d.get("authorization") or {}),
            plan=d.get("plan"),
            result=dict(d.get("result") or {}),
            evidence_refs=list(d.get("evidence_refs") or []),
            operation_ids=list(d.get("operation_ids") or []),
            job_ids=list(d.get("job_ids") or []),
            error=d.get("error"),
            recovery=dict(d.get("recovery") or {}),
            created_at=str(d.get("created_at") or _now()),
            updated_at=str(d.get("updated_at") or _now()),
        )


class AgentTaskStore:
    """Process + disk durable store for governed agent tasks (tests + single-node)."""

    def __init__(self) -> None:
        self._by_id: dict[str, GovernedAgentTask] = {}
        self._by_idem: dict[str, str] = {}

    def put(self, task: GovernedAgentTask) -> GovernedAgentTask:
        task.updated_at = _now()
        self._by_id[task.task_id] = task
        if task.idempotency_key:
            self._by_idem[f"{task.owner_id}:{task.idempotency_key}"] = task.task_id
        return task

    def get(self, task_id: str) -> Optional[GovernedAgentTask]:
        return self._by_id.get(task_id)

    def get_by_idempotency(self, owner_id: str, key: str) -> Optional[GovernedAgentTask]:
        tid = self._by_idem.get(f"{owner_id}:{key}")
        return self._by_id.get(tid) if tid else None


_STORE = AgentTaskStore()


def get_agent_task_store() -> AgentTaskStore:
    return _STORE


def reset_agent_task_store_for_tests() -> AgentTaskStore:
    global _STORE
    _STORE = AgentTaskStore()
    return _STORE


class DelegationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _reject_client_grants(requested: Any) -> None:
    """Client-supplied grant objects are never authoritative."""
    if not requested:
        return
    if isinstance(requested, dict) and requested.get("client_supplied_grants"):
        raise DelegationError("CLIENT_GRANT_REJECTED", "client_supplied_grants are never accepted")
    if isinstance(requested, list):
        for item in requested:
            if isinstance(item, dict) and (
                item.get("client_grant") or item.get("grant_token") or item.get("elevated")
            ):
                raise DelegationError("CLIENT_GRANT_REJECTED", "client capability grants rejected")


def resolve_and_authorize_capabilities(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    requested_capabilities: list[str],
    client_supplied_grants: bool = False,
) -> list[CapabilityGrant]:
    """Authorize each capability id via CapabilitySubstrate (UCIP path)."""
    if client_supplied_grants:
        raise DelegationError("CLIENT_GRANT_REJECTED", "client_supplied_grants=True denied")
    from governance.capability_substrate import get_capability_substrate, InvocationContext

    sub = get_capability_substrate()
    grants: list[CapabilityGrant] = []
    for cap_id in requested_capabilities or []:
        cid = str(cap_id).strip()
        if not cid:
            continue
        low = cid.lower()
        if any(x in low for x in ("execute_anything", "unrestricted", "shell_raw", "sudo")):
            grants.append(CapabilityGrant(cid, False, "capability_forbidden", "server"))
            continue
        try:
            contract = sub.resolve(cid)
            if contract is None:
                # Allow delegating known meta ids that register at boot
                if cid.startswith("devos."):
                    grants.append(CapabilityGrant(cid, True, "devos_namespace_delegable", "server"))
                else:
                    grants.append(CapabilityGrant(cid, False, "unknown_capability", "server"))
                continue
            # Delegation is the grant boundary; UCIP re-checked at request_capability
            grants.append(CapabilityGrant(cid, True, "delegable", "server"))
        except Exception as e:
            grants.append(CapabilityGrant(cid, False, f"auth_error:{type(e).__name__}", "server"))
    return grants


def delegate_agent_task(
    *,
    owner_id: str,
    agent_type: str = "worker",
    task_input: Optional[dict] = None,
    requested_capabilities: Optional[list[str]] = None,
    parent_operation_id: Optional[str] = None,
    parent_run_id: Optional[str] = None,
    parent_step_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    workflow_version: Optional[int] = None,
    tenant_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    client_supplied_grants: bool = False,
    logical_key: Optional[str] = None,
) -> GovernedAgentTask:
    """Create or return idempotent governed agent task. Does not execute side effects."""
    if not owner_id:
        raise DelegationError("OWNER_REQUIRED", "owner_id required")
    if client_supplied_grants:
        raise DelegationError("CLIENT_GRANT_REJECTED", "client_supplied_grants denied")
    _reject_client_grants(task_input)
    _reject_client_grants(requested_capabilities)

    store = get_agent_task_store()
    ikey = idempotency_key or agent_task_idempotency_key(
        owner_id=owner_id,
        tenant_id=tenant_id,
        parent_run_id=parent_run_id,
        agent_type=agent_type,
        logical_key=logical_key or json.dumps(task_input or {}, sort_keys=True)[:200],
    )
    existing = store.get_by_idempotency(owner_id, ikey)
    if existing:
        return existing

    caps = list(requested_capabilities or [])
    grants = resolve_and_authorize_capabilities(
        owner_id=owner_id,
        tenant_id=tenant_id,
        requested_capabilities=caps,
        client_supplied_grants=False,
    )
    allowed = [g.capability_id for g in grants if g.authorized]
    denied = [g for g in grants if not g.authorized]

    task = GovernedAgentTask(
        task_id=_id(),
        owner_id=owner_id,
        tenant_id=tenant_id,
        agent_type=agent_type,
        status=AgentTaskLifecycle.PENDING,
        task_input=dict(task_input or {}),
        allowed_capabilities=allowed,
        parent_run_id=parent_run_id,
        parent_step_id=parent_step_id,
        parent_operation_id=parent_operation_id,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        idempotency_key=ikey,
        authorization={
            "grants": [asdict(g) for g in grants],
            "denied": [asdict(g) for g in denied],
            "client_supplied_grants": False,
        },
    )
    if denied and not allowed and caps:
        task.status = AgentTaskLifecycle.FAILED
        task.error = "all_capabilities_denied"
    store.put(task)
    return task


def assert_owner(task: GovernedAgentTask, owner_id: str, tenant_id: Optional[str] = None) -> None:
    if task.owner_id != owner_id:
        raise DelegationError("OWNER_ISOLATION", "cross-owner agent task access denied")
    if tenant_id is not None and task.tenant_id and task.tenant_id != tenant_id:
        raise DelegationError("TENANT_ISOLATION", "cross-tenant agent task access denied")


def record_plan(task: GovernedAgentTask, plan: dict) -> GovernedAgentTask:
    """Planning/reasoning only — never treated as execution evidence."""
    if task.status in TERMINAL:
        return task
    task.plan = dict(plan or {})
    task.status = AgentTaskLifecycle.PLANNING
    # Explicit: plan text is not evidence
    task.result.setdefault("plan_is_not_evidence", True)
    return get_agent_task_store().put(task)


async def request_capability(
    task: GovernedAgentTask,
    *,
    capability_id: str,
    inputs: Optional[dict] = None,
    execute: bool = False,
) -> CapabilityRequestRecord:
    """Request a capability for an agent task through UCIP substrate.

    If execute=False, only authorize (AWAITING_CAPABILITY / AUTHORIZED).
    If execute=True, invoke substrate after auth (creates real evidence path only via substrate).
    """
    assert_owner(task, task.owner_id, task.tenant_id)
    if task.status == AgentTaskLifecycle.UNKNOWN:
        raise DelegationError("UNKNOWN_BLOCKED", "cannot execute while task is UNKNOWN")
    if task.status == AgentTaskLifecycle.CANCELLED:
        raise DelegationError("CANCELLED", "task cancelled")

    cid = str(capability_id).strip()
    if cid not in (task.allowed_capabilities or []):
        rec = CapabilityRequestRecord(
            capability_id=cid,
            inputs=dict(inputs or {}),
            status="denied",
            error="capability_not_delegated",
        )
        task.capability_requests.append(rec)
        task.status = AgentTaskLifecycle.AWAITING_CAPABILITY
        get_agent_task_store().put(task)
        return rec

    from governance.capability_substrate import (
        get_capability_substrate,
        InvocationRequest,
        InvocationContext,
        InvocationStatus,
    )

    task.status = AgentTaskLifecycle.AWAITING_CAPABILITY
    get_agent_task_store().put(task)

    sub = get_capability_substrate()
    # Grant only the delegated capability set for this task
    # project_id from task_input only (server-bound), never planner workspace_root
    _ti = dict(task.task_input or {})
    _project = str(_ti.get("project_id") or _ti.get("workspace_id") or "default")
    ctx = InvocationContext(
        tenant_id=str(task.tenant_id or ""),
        owner_id=task.owner_id,
        granted_capabilities=set(task.allowed_capabilities or []),
        actor_type="agent",
        actor_id=task.owner_id,
        surface="automation",
        correlation_id=task.parent_run_id or task.task_id,
        client_supplied_grants=False,
        metadata={"project_id": _project},
    )
    req = InvocationRequest(
        capability_id=cid,
        inputs=dict(inputs or {}),
        context=ctx,
        dry_run=not execute,
    )
    result = await sub.invoke(req)
    st_val = result.status.value if hasattr(result.status, "value") else str(result.status)

    rec = CapabilityRequestRecord(
        capability_id=cid,
        inputs=dict(inputs or {}),
        status="denied" if st_val == "denied" else ("authorized" if not execute else "executed"),
        error=None if st_val != "denied" else (getattr(result, "reason", None) or "denied"),
        result=getattr(result, "outputs", None) or getattr(result, "data", None),
    )
    # Link operation if substrate exposed one
    meta = getattr(result, "metadata", None) or {}
    outputs = getattr(result, "outputs", None) or {}
    if isinstance(meta, dict):
        rec.operation_id = meta.get("operation_id") or (outputs.get("operation_id") if isinstance(outputs, dict) else None)
        rec.job_id = meta.get("job_id") or (outputs.get("job_id") if isinstance(outputs, dict) else None)
        if meta.get("evidence_id"):
            rec.evidence_refs.append(meta["evidence_id"])
        if isinstance(outputs, dict) and outputs.get("evidence_id"):
            rec.evidence_refs.append(outputs["evidence_id"])
    elif isinstance(outputs, dict):
        rec.operation_id = outputs.get("operation_id")
        rec.job_id = outputs.get("job_id")
        if outputs.get("evidence_id"):
            rec.evidence_refs.append(outputs["evidence_id"])
    if rec.operation_id:
        task.operation_ids.append(rec.operation_id)
    if rec.job_id:
        task.job_ids.append(rec.job_id)
    task.capability_requests.append(rec)

    if st_val == "denied":
        task.status = AgentTaskLifecycle.AWAITING_CAPABILITY
    elif not execute:
        task.status = AgentTaskLifecycle.AUTHORIZED
    else:
        task.status = AgentTaskLifecycle.RUNNING
        # Only substrate evidence counts
        if rec.evidence_refs:
            task.evidence_refs.extend(rec.evidence_refs)
    get_agent_task_store().put(task)
    return rec


def mark_unknown(task: GovernedAgentTask, *, reason: str = "UNKNOWN_SIDE_EFFECT") -> GovernedAgentTask:
    """Consequential crash after side effect — no auto-retry."""
    task.status = AgentTaskLifecycle.UNKNOWN
    task.error = reason
    task.recovery = {
        "auto_retry": False,
        "reason": reason,
        "pending_review": True,
    }
    return get_agent_task_store().put(task)


def mark_completed(
    task: GovernedAgentTask,
    *,
    result: Optional[dict] = None,
    evidence_refs: Optional[list] = None,
) -> GovernedAgentTask:
    if task.status == AgentTaskLifecycle.UNKNOWN:
        raise DelegationError("UNKNOWN_BLOCKED", "cannot complete UNKNOWN task without review")
    # Free-form claims are not evidence
    res = dict(result or {})
    for bad in ("done", "verified", "executed successfully", "success"):
        if str(res.get("claim", "")).lower() == bad:
            res["claim_ignored"] = True
            res.pop("claim", None)
    task.result = res
    if evidence_refs:
        # only append explicit refs — agent strings alone are insufficient
        task.evidence_refs.extend([e for e in evidence_refs if e])
    task.status = AgentTaskLifecycle.COMPLETED
    return get_agent_task_store().put(task)


def mark_failed(task: GovernedAgentTask, error: str) -> GovernedAgentTask:
    task.status = AgentTaskLifecycle.FAILED
    task.error = error
    return get_agent_task_store().put(task)


def cancel_task(task: GovernedAgentTask) -> GovernedAgentTask:
    if task.status in (AgentTaskLifecycle.COMPLETED, AgentTaskLifecycle.UNKNOWN):
        return task
    task.status = AgentTaskLifecycle.CANCELLED
    return get_agent_task_store().put(task)


def reject_fabricated_evidence(claim: Any) -> bool:
    """Return True if claim must be ignored as non-evidence."""
    if claim is None:
        return True
    if isinstance(claim, str):
        low = claim.strip().lower()
        return low in ("done", "verified", "executed successfully", "success", "ok", "complete")
    if isinstance(claim, dict) and not claim.get("evidence_id") and not claim.get("operation_id"):
        if claim.get("claim") or claim.get("message"):
            return True
    return False


async def execute_agent_step_body(
    step_inputs: dict,
    *,
    owner_id: str,
    tenant_id: Optional[str] = None,
    parent_run_id: Optional[str] = None,
    parent_step_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    workflow_version: Optional[int] = None,
    job_id: Optional[str] = None,
) -> dict:
    """Workflow AGENT step body — governed delegation + optional capability requests.

    Does not run unrestricted tools. Capability execution only via request_capability.
    """
    if not owner_id:
        return {
            "status": "failed",
            "error": "owner_required",
            "error_code": "AUTHORIZATION_FAILURE",
        }
    agent_type = str(step_inputs.get("agent_type") or "worker")
    requested = list(step_inputs.get("capabilities") or step_inputs.get("allowed_capabilities") or [])
    # Default: allow only safe inspect/list meta capabilities if none specified
    if not requested:
        requested = ["devos.capability.list"]

    if step_inputs.get("client_supplied_grants"):
        return {
            "status": "failed",
            "error": "client_supplied_grants denied",
            "error_code": "AUTHORIZATION_FAILURE",
        }

    try:
        task = delegate_agent_task(
            owner_id=owner_id,
            agent_type=agent_type,
            task_input=dict(step_inputs.get("task_input") or step_inputs),
            requested_capabilities=requested,
            parent_run_id=parent_run_id,
            parent_step_id=parent_step_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            tenant_id=tenant_id,
            idempotency_key=step_inputs.get("idempotency_key"),
            logical_key=step_inputs.get("logical_key") or parent_step_id,
            client_supplied_grants=False,
        )
    except DelegationError as e:
        return {
            "status": "failed",
            "error": e.message,
            "error_code": e.code,
        }

    if task.status == AgentTaskLifecycle.FAILED:
        return {
            "status": "failed",
            "error": task.error or "delegation_failed",
            "error_code": "AUTHORIZATION_FAILURE",
            "task_id": task.task_id,
            "task": task.to_dict(),
        }

    # Optional plan (not evidence)
    if step_inputs.get("plan"):
        record_plan(task, dict(step_inputs["plan"]))

    # Execute capability requests listed in step (bounded)
    exec_caps = list(step_inputs.get("execute_capabilities") or [])
    results = []
    for cid in exec_caps[:10]:
        if cid not in task.allowed_capabilities:
            results.append({"capability_id": cid, "status": "denied", "error": "not_delegated"})
            continue
        rec = await request_capability(
            task,
            capability_id=cid,
            inputs=dict(step_inputs.get("capability_inputs") or {}),
            execute=True,
        )
        results.append(rec.to_dict())
        if rec.status == "denied":
            mark_failed(task, rec.error or "capability_denied")
            return {
                "status": "failed",
                "error": rec.error,
                "error_code": "AUTHORIZATION_FAILURE",
                "task_id": task.task_id,
                "capability_results": results,
            }

    # Fabricated success strings from agent inputs are ignored
    if reject_fabricated_evidence(step_inputs.get("claim")):
        step_inputs = {**step_inputs, "claim_ignored": True}

    # Bind completion contract from task_input if present; validate before SUCCEEDED
    from brain.agentic_runtime import (
        CompletionContract,
        TurnDecision,
        set_completion_contract,
        try_complete,
        checkpoint_from_task,
        apply_transition,
        AgentRuntimeState,
        persist_checkpoint,
    )
    raw_cc = (task.task_input or {}).get("completion_contract")
    if isinstance(raw_cc, dict):
        set_completion_contract(task, CompletionContract.from_dict(raw_cc))

    # Mirror capability results into recovery for validator
    task.recovery = dict(task.recovery or {})
    hist = []
    for r in results:
        if r.get("status") == "executed":
            hist.append({
                "capability_id": r.get("capability_id"),
                "status": "executed",
                "evidence_refs": r.get("evidence_refs") or [],
            })
    if hist:
        task.recovery["observation_history"] = hist
        for r in results:
            if r.get("status") == "executed" and r.get("evidence_refs"):
                task.evidence_refs.extend(r["evidence_refs"])

    cp = checkpoint_from_task(task)
    # Capability wave finished — not an in-flight operation for completion purposes
    cp.state = AgentRuntimeState.PLANNING
    cp.pending_request = None
    persist_checkpoint(task, cp)
    cp = checkpoint_from_task(task)
    decision = TurnDecision(kind="complete", complete=True, reason="agent_step")
    task, cp, ok = try_complete(task, cp, decision)
    if not ok:
        mark_failed(task, (cp.failure or "completion_contract_unsatisfied"))
        return {
            "status": "failed",
            "error": cp.failure or "completion_contract_unsatisfied",
            "error_code": "COMPLETION_REJECTED",
            "task_id": task.task_id,
            "capability_results": results,
            "completion_validation": (task.recovery or {}).get("last_completion_validation"),
        }

    return {
        "status": "succeeded",
        "task_id": task.task_id,
        "task": task.to_dict(),
        "capability_results": results,
        "evidence_refs": list(task.evidence_refs),
        "operation_ids": list(task.operation_ids),
        "outputs": {
            "task_id": task.task_id,
            "status": task.status.value if hasattr(task.status, "value") else str(task.status),
            "allowed_capabilities": task.allowed_capabilities,
        },
    }
