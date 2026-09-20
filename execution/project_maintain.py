"""
Governed project.maintain capability.

Five independent state domains:
  Task / Operation / Observation / Maintenance Request / Maintenance Action

«Observe establishes what is happening. A maintenance request establishes what
 governed maintenance is being considered. Maintenance actions define bounded
 work. Authorization permits that work. Existing capabilities execute it.
 Verification establishes the result. Evidence proves it.»

Maintain orchestrates existing capabilities through the capability substrate.
It is NOT a second execution engine, queue, or evidence store.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Optional

from execution.files import FileService, PathViolation
from governance.security_policy import (
    SecurityPolicyError,
    reject_planner_forbidden_fields,
    require_owner_id,
    require_project_id,
    assert_ownership_match,
)

logger = logging.getLogger("devos.project_maintain")

CAP_PROJECT_MAINTAIN = "project.maintain"
PROFILE_MAINTAIN = "maintain"
MAINTAIN_CONTRACT = "devos.maintain.json"
MAINTAIN_DIR = ".devos/maintenance"

MAX_ACTIONS_PER_REQUEST = 16
MAX_REPLANS = 3

# ── Request statuses ──────────────────────────────────────────────────────────
REQ_DETECTED = "DETECTED"
REQ_OPEN = "OPEN"
REQ_EVALUATING = "EVALUATING"
REQ_PLANNED = "PLANNED"
REQ_AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
REQ_AUTHORIZED = "AUTHORIZED"
REQ_EXECUTING = "EXECUTING"
REQ_VERIFYING = "VERIFYING"
REQ_RESOLVED = "RESOLVED"
REQ_REJECTED = "REJECTED"
REQ_BLOCKED = "BLOCKED"
REQ_FAILED = "FAILED"
REQ_CANCELLED = "CANCELLED"
REQ_UNKNOWN = "UNKNOWN"

REQUEST_TRANSITIONS: dict[str, frozenset[str]] = {
    REQ_DETECTED: frozenset({REQ_OPEN, REQ_REJECTED}),
    REQ_OPEN: frozenset({REQ_EVALUATING, REQ_CANCELLED}),
    REQ_EVALUATING: frozenset({REQ_PLANNED, REQ_BLOCKED, REQ_REJECTED, REQ_CANCELLED}),
    REQ_PLANNED: frozenset({REQ_AWAITING_AUTHORIZATION, REQ_BLOCKED, REQ_CANCELLED}),
    REQ_AWAITING_AUTHORIZATION: frozenset({REQ_AUTHORIZED, REQ_REJECTED, REQ_CANCELLED}),
    REQ_AUTHORIZED: frozenset({REQ_EXECUTING, REQ_CANCELLED}),
    REQ_EXECUTING: frozenset({REQ_VERIFYING, REQ_FAILED, REQ_BLOCKED, REQ_UNKNOWN, REQ_CANCELLED}),
    REQ_VERIFYING: frozenset({REQ_RESOLVED, REQ_EXECUTING, REQ_FAILED, REQ_BLOCKED, REQ_UNKNOWN, REQ_CANCELLED}),
    REQ_RESOLVED: frozenset(),
    REQ_REJECTED: frozenset(),
    REQ_BLOCKED: frozenset({REQ_EVALUATING, REQ_CANCELLED}),
    REQ_FAILED: frozenset(),
    REQ_CANCELLED: frozenset(),
    REQ_UNKNOWN: frozenset(),  # explicit reconciliation only
}

# ── Action statuses ───────────────────────────────────────────────────────────
ACT_PENDING = "PENDING"
ACT_AUTHORIZED = "AUTHORIZED"
ACT_EXECUTING = "EXECUTING"
ACT_VERIFYING = "VERIFYING"
ACT_SUCCEEDED = "SUCCEEDED"
ACT_FAILED = "FAILED"
ACT_BLOCKED = "BLOCKED"
ACT_CANCELLED = "CANCELLED"
ACT_SKIPPED = "SKIPPED"
ACT_UNKNOWN = "UNKNOWN"

ACTION_TRANSITIONS: dict[str, frozenset[str]] = {
    ACT_PENDING: frozenset({ACT_AUTHORIZED, ACT_BLOCKED, ACT_CANCELLED, ACT_SKIPPED}),
    ACT_AUTHORIZED: frozenset({ACT_EXECUTING, ACT_CANCELLED}),
    ACT_EXECUTING: frozenset({ACT_VERIFYING, ACT_SUCCEEDED, ACT_FAILED, ACT_UNKNOWN, ACT_CANCELLED}),
    ACT_VERIFYING: frozenset({ACT_SUCCEEDED, ACT_FAILED, ACT_UNKNOWN}),
    ACT_SUCCEEDED: frozenset(),
    ACT_FAILED: frozenset(),
    ACT_BLOCKED: frozenset({ACT_PENDING, ACT_CANCELLED}),
    ACT_CANCELLED: frozenset(),
    ACT_SKIPPED: frozenset(),
    ACT_UNKNOWN: frozenset(),
}

# Observation trigger vocabulary (aligned with project.observe)
TRIGGER_KINDS = frozenset({
    "runtime_state", "readiness", "health", "artifact_identity", "deployment_identity",
})
OBS_STATUSES = frozenset({
    "OBSERVED", "UNHEALTHY", "UNREADY", "DEGRADED", "UNAVAILABLE", "FAILED", "HEALTHY",
})

ALLOWED_MAINTAIN_CAPS = frozenset({
    "artifact.write", "artifact.delete",
    "project.validate", "project.build", "project.preview",
    "project.verify", "project.deploy", "project.observe",
})

# Default maintenance eligibility metadata for known capabilities
DEFAULT_MAINTENANCE_META: dict[str, dict] = {
    "project.build": {
        "can_be_maintenance_action": True,
        "can_verify_maintenance": False,
        "verification_scope": "build_output",
    },
    "project.validate": {
        "can_be_maintenance_action": True,
        "can_verify_maintenance": True,
        "verification_scope": "validation",
    },
    "project.verify": {
        "can_be_maintenance_action": True,
        "can_verify_maintenance": True,
        "verification_scope": "verification",
    },
    "project.observe": {
        "can_be_maintenance_action": True,
        "can_verify_maintenance": True,
        "verification_scope": "observation",
    },
    "project.preview": {
        "can_be_maintenance_action": True,
        "can_verify_maintenance": False,
        "verification_scope": "preview",
    },
    "project.deploy": {
        "can_be_maintenance_action": True,
        "can_verify_maintenance": False,
        "verification_scope": "deployment",
    },
    "artifact.write": {
        "can_be_maintenance_action": True,
        "can_verify_maintenance": False,
        "verification_scope": "artifact",
    },
    "artifact.delete": {
        "can_be_maintenance_action": True,
        "can_verify_maintenance": False,
        "verification_scope": "artifact",
    },
}

POLICY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ACTION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_LOCK = threading.RLock()
_EVIDENCE: list[dict] = []  # durable evidence log for tests (also written under .devos)


class ProjectMaintainError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _policy(e: SecurityPolicyError) -> ProjectMaintainError:
    return ProjectMaintainError(e.code, e.message)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _digest(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def reset_maintain_store_for_tests() -> None:
    global _EVIDENCE
    with _LOCK:
        _EVIDENCE = []


def _read_json(fs: FileService, rel: str) -> Optional[dict]:
    try:
        data = fs.read(rel)
        raw = data.get("content") if isinstance(data, dict) else None
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _write_json(fs: FileService, rel: str, data: dict) -> None:
    fs.write(rel, json.dumps(data, indent=2, sort_keys=True, default=str))


def _req_path(mid: str) -> str:
    return f"{MAINTAIN_DIR}/requests/{mid}.json"


def _idx_path() -> str:
    return f"{MAINTAIN_DIR}/index.json"


def _evidence_path() -> str:
    return f"{MAINTAIN_DIR}/evidence.jsonl"


def _append_evidence(fs: FileService, event: dict) -> None:
    event = dict(event)
    event.setdefault("timestamp", _now())
    event.setdefault("evidence_id", "ev_" + uuid.uuid4().hex[:12])
    with _LOCK:
        _EVIDENCE.append(event)
    try:
        line = json.dumps(event, sort_keys=True, default=str) + "\n"
        existing = ""
        try:
            data = fs.read(_evidence_path())
            existing = (data.get("content") if isinstance(data, dict) else "") or ""
        except Exception:
            existing = ""
        fs.write(_evidence_path(), existing + line)
    except Exception as e:
        logger.debug("evidence append failed: %s", type(e).__name__)


def list_maintain_evidence() -> list[dict]:
    with _LOCK:
        return list(_EVIDENCE)


# ── Contract validation ───────────────────────────────────────────────────────

def load_and_validate_maintain_contract(fs: FileService) -> dict:
    """Validate exact devos.maintain.json schema."""
    data = _read_json(fs, MAINTAIN_CONTRACT)
    if not data:
        raise ProjectMaintainError("CONTRACT_MISSING", f"{MAINTAIN_CONTRACT} not found")
    return validate_maintain_contract(data)


def validate_maintain_contract(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ProjectMaintainError("CONTRACT_INVALID", "contract must be object")
    allowed_top = {"version", "profile", "policies"}
    unknown = set(data.keys()) - allowed_top
    if unknown:
        raise ProjectMaintainError("CONTRACT_UNKNOWN_FIELD", f"unknown fields: {sorted(unknown)}")
    if data.get("version") != 1:
        raise ProjectMaintainError("CONTRACT_VERSION", "version must be 1")
    if data.get("profile") != PROFILE_MAINTAIN:
        raise ProjectMaintainError("CONTRACT_PROFILE", "profile must be maintain")
    policies = data.get("policies")
    if not isinstance(policies, list) or not policies:
        raise ProjectMaintainError("CONTRACT_POLICIES", "policies must be non-empty list")
    seen_pids: set[str] = set()
    for pol in policies:
        if not isinstance(pol, dict):
            raise ProjectMaintainError("CONTRACT_POLICY", "policy must be object")
        p_allowed = {"id", "trigger", "actions"}
        unk = set(pol.keys()) - p_allowed
        if unk:
            raise ProjectMaintainError("CONTRACT_UNKNOWN_FIELD", f"policy unknown fields: {sorted(unk)}")
        pid = str(pol.get("id") or "")
        if not POLICY_ID_RE.match(pid):
            raise ProjectMaintainError("CONTRACT_POLICY_ID", f"invalid policy id: {pid}")
        if pid in seen_pids:
            raise ProjectMaintainError("CONTRACT_DUPLICATE_ID", f"duplicate policy id: {pid}")
        seen_pids.add(pid)
        trig = pol.get("trigger")
        if not isinstance(trig, dict):
            raise ProjectMaintainError("CONTRACT_TRIGGER", "trigger must be object")
        t_allowed = {"source", "kind", "status"}
        if set(trig.keys()) - t_allowed:
            raise ProjectMaintainError("CONTRACT_TRIGGER", "invalid trigger fields")
        if trig.get("source") != "observation":
            raise ProjectMaintainError("CONTRACT_TRIGGER", "trigger.source must be observation")
        if trig.get("kind") not in TRIGGER_KINDS:
            raise ProjectMaintainError("CONTRACT_TRIGGER", f"invalid trigger.kind: {trig.get('kind')}")
        if trig.get("status") not in OBS_STATUSES:
            raise ProjectMaintainError("CONTRACT_TRIGGER", f"invalid trigger.status: {trig.get('status')}")
        actions = pol.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ProjectMaintainError("CONTRACT_ACTIONS", "actions must be non-empty list")
        if len(actions) > MAX_ACTIONS_PER_REQUEST:
            raise ProjectMaintainError("CONTRACT_ACTIONS", f"max {MAX_ACTIONS_PER_REQUEST} actions")
        seen_aids: set[str] = set()
        for act in actions:
            if not isinstance(act, dict):
                raise ProjectMaintainError("CONTRACT_ACTION", "action must be object")
            a_allowed = {"id", "capability", "required", "verification"}
            if set(act.keys()) - a_allowed:
                raise ProjectMaintainError("CONTRACT_UNKNOWN_FIELD", "action has unknown fields")
            aid = str(act.get("id") or "")
            if not ACTION_ID_RE.match(aid):
                raise ProjectMaintainError("CONTRACT_ACTION_ID", f"invalid action id: {aid}")
            if aid in seen_aids:
                raise ProjectMaintainError("CONTRACT_DUPLICATE_ID", f"duplicate action id: {aid}")
            seen_aids.add(aid)
            cap = str(act.get("capability") or "")
            if cap not in ALLOWED_MAINTAIN_CAPS:
                raise ProjectMaintainError("CONTRACT_CAPABILITY", f"unknown/disallowed capability: {cap}")
            if "required" not in act or not isinstance(act["required"], bool):
                raise ProjectMaintainError("CONTRACT_REQUIRED", "action.required must be boolean")
            ver = act.get("verification")
            if ver is not None:
                if not isinstance(ver, dict):
                    raise ProjectMaintainError("CONTRACT_VERIFICATION", "verification must be object")
                v_allowed = {"required", "capability", "ordering"}
                if set(ver.keys()) - v_allowed:
                    raise ProjectMaintainError("CONTRACT_VERIFICATION", "unknown verification fields")
                if "required" not in ver or not isinstance(ver["required"], bool):
                    raise ProjectMaintainError("CONTRACT_VERIFICATION", "verification.required must be bool")
                vcap = str(ver.get("capability") or "")
                if vcap and vcap not in ALLOWED_MAINTAIN_CAPS:
                    raise ProjectMaintainError(
                        "CONTRACT_VERIFICATION", f"unsupported verification capability: {vcap}"
                    )
                if ver.get("ordering") not in (None, "after"):
                    raise ProjectMaintainError("CONTRACT_VERIFICATION", "ordering must be after")
    return data


def write_default_maintain_contract(fs: FileService) -> dict:
    """Write the canonical repair-build policy."""
    data = {
        "version": 1,
        "profile": "maintain",
        "policies": [
            {
                "id": "repair-build",
                "trigger": {
                    "source": "observation",
                    "kind": "health",
                    "status": "UNHEALTHY",
                },
                "actions": [
                    {
                        "id": "rebuild",
                        "capability": "project.build",
                        "required": True,
                        "verification": {
                            "required": True,
                            "capability": "project.verify",
                            "ordering": "after",
                        },
                    },
                    {
                        "id": "observe",
                        "capability": "project.observe",
                        "required": True,
                        "verification": {
                            "required": True,
                            "capability": "project.observe",
                            "ordering": "after",
                        },
                    },
                ],
            }
        ],
    }
    validate_maintain_contract(data)
    _write_json(fs, MAINTAIN_CONTRACT, data)
    return data


# ── State transitions (CAS) ───────────────────────────────────────────────────

def _can_transition(table: dict[str, frozenset[str]], current: str, nxt: str) -> bool:
    return nxt in table.get(current, frozenset())


def transition_request(req: dict, new_status: str, *, reason: str = "") -> dict:
    cur = req.get("status")
    if not _can_transition(REQUEST_TRANSITIONS, cur, new_status):
        raise ProjectMaintainError(
            "INVALID_TRANSITION",
            f"request cannot transition {cur} → {new_status}",
        )
    req = dict(req)
    req["status"] = new_status
    req["updated_at"] = _now()
    req["version"] = int(req.get("version") or 0) + 1
    if reason:
        req["last_transition_reason"] = reason
    if new_status == REQ_RESOLVED:
        req["resolved_at"] = _now()
    if new_status == REQ_CANCELLED:
        req["cancelled_at"] = _now()
    if new_status == REQ_FAILED and reason:
        req["failure_reason"] = reason
    if new_status == REQ_BLOCKED and reason:
        req["blocked_reason"] = reason
    if new_status == REQ_UNKNOWN and reason:
        req["unknown_reason"] = reason
    return req


def transition_action(act: dict, new_status: str, *, reason: str = "") -> dict:
    cur = act.get("status")
    if not _can_transition(ACTION_TRANSITIONS, cur, new_status):
        raise ProjectMaintainError(
            "INVALID_TRANSITION",
            f"action cannot transition {cur} → {new_status}",
        )
    if act.get("required") is True and cur == ACT_PENDING and new_status == ACT_SKIPPED:
        raise ProjectMaintainError(
            "REQUIRED_NOT_SKIPPABLE",
            "required actions cannot transition PENDING → SKIPPED",
        )
    act = dict(act)
    act["status"] = new_status
    act["updated_at"] = _now()
    act["version"] = int(act.get("version") or 0) + 1
    if reason:
        act["last_transition_reason"] = reason
    if new_status in (ACT_SUCCEEDED, ACT_FAILED, ACT_CANCELLED, ACT_SKIPPED):
        act["completed_at"] = _now()
    if new_status == ACT_FAILED and reason:
        act["failure_reason"] = reason
    if new_status == ACT_BLOCKED and reason:
        act["blocked_reason"] = reason
    if new_status == ACT_UNKNOWN and reason:
        act["unknown_reason"] = reason
    return act


# ── Persistence ───────────────────────────────────────────────────────────────

def _save_request(fs: FileService, req: dict) -> dict:
    mid = req["maintenance_request_id"]
    with _LOCK:
        existing = _read_json(fs, _req_path(mid))
        if existing and int(existing.get("version") or 0) > int(req.get("version") or 0):
            raise ProjectMaintainError("CAS_CONFLICT", "concurrent update rejected")
        _write_json(fs, _req_path(mid), req)
        idx = _read_json(fs, _idx_path()) or {"requests": []}
        ids = list(idx.get("requests") or [])
        if mid not in ids:
            ids.append(mid)
            idx["requests"] = ids
            _write_json(fs, _idx_path(), idx)
    return req


def load_request(fs: FileService, mid: str) -> Optional[dict]:
    return _read_json(fs, _req_path(mid))


def find_open_request_for_observation(fs: FileService, observation_id: str) -> Optional[dict]:
    idx = _read_json(fs, _idx_path()) or {}
    for mid in idx.get("requests") or []:
        req = load_request(fs, mid)
        if not req:
            continue
        if req.get("source_observation_id") == observation_id:
            if req.get("status") not in (
                REQ_RESOLVED, REQ_REJECTED, REQ_CANCELLED, REQ_FAILED,
            ):
                return req
    return None


# ── Core API ──────────────────────────────────────────────────────────────────

def create_maintenance_request(
    fs: FileService,
    *,
    owner_id: str,
    project_id: str,
    deployment_id: str,
    source_observation_id: str,
    observation_kind: str,
    observation_status: str,
    policy_id: Optional[str] = None,
) -> dict:
    """Create DETECTED request from trusted observation context (not planner)."""
    try:
        require_owner_id(owner_id)
        require_project_id(project_id)
    except SecurityPolicyError as e:
        raise _policy(e) from e
    if not deployment_id or not source_observation_id:
        raise ProjectMaintainError("IDENTITY_REQUIRED", "deployment_id and source_observation_id required")
    if observation_kind not in TRIGGER_KINDS:
        raise ProjectMaintainError("TRIGGER_KIND", f"invalid observation kind: {observation_kind}")
    if observation_status not in OBS_STATUSES:
        raise ProjectMaintainError("TRIGGER_STATUS", f"invalid observation status: {observation_status}")

    # Idempotency: same observation → converge
    existing = find_open_request_for_observation(fs, source_observation_id)
    if existing:
        return existing

    try:
        contract = load_and_validate_maintain_contract(fs)
    except ProjectMaintainError:
        contract = write_default_maintain_contract(fs)

    matched = None
    for pol in contract["policies"]:
        trig = pol["trigger"]
        if (
            trig["kind"] == observation_kind
            and trig["status"] == observation_status
            and (policy_id is None or pol["id"] == policy_id)
        ):
            matched = pol
            break
    if matched is None:
        raise ProjectMaintainError(
            "NO_POLICY_MATCH",
            f"no policy for observation {observation_kind}/{observation_status}",
        )

    mid = "mr_" + uuid.uuid4().hex[:16]
    now = _now()
    req = {
        "maintenance_request_id": mid,
        "owner_id": owner_id,
        "project_id": project_id,
        "deployment_id": deployment_id,
        "source_observation_id": source_observation_id,
        "policy_id": matched["id"],
        "policy_version": 1,
        "status": REQ_DETECTED,
        "created_at": now,
        "updated_at": now,
        "version": 1,
        "replan_count": 0,
        "actions": [],
        "trigger": dict(matched["trigger"]),
        "policy_actions": list(matched["actions"]),
    }
    _save_request(fs, req)
    _append_evidence(fs, {
        "event": "maintenance_request_created",
        "maintenance_request_id": mid,
        "owner_id": owner_id,
        "project_id": project_id,
        "source_observation_id": source_observation_id,
        "status": REQ_DETECTED,
    })
    return req


def evaluate_maintenance_request(fs: FileService, mid: str) -> dict:
    req = load_request(fs, mid)
    if not req:
        raise ProjectMaintainError("NOT_FOUND", mid)
    if req["status"] == REQ_DETECTED:
        req = transition_request(req, REQ_OPEN, reason="evaluate")
        _save_request(fs, req)
    if req["status"] == REQ_OPEN:
        req = transition_request(req, REQ_EVALUATING, reason="evaluate")
        _save_request(fs, req)
    return req


def plan_maintenance_request(fs: FileService, mid: str) -> dict:
    req = load_request(fs, mid)
    if not req:
        raise ProjectMaintainError("NOT_FOUND", mid)
    if req["status"] == REQ_DETECTED:
        req = transition_request(req, REQ_OPEN)
        _save_request(fs, req)
    if req["status"] == REQ_OPEN:
        req = transition_request(req, REQ_EVALUATING)
        _save_request(fs, req)
    if req["status"] not in (REQ_EVALUATING, REQ_BLOCKED):
        if req["status"] == REQ_PLANNED:
            return req
        raise ProjectMaintainError("INVALID_STATE", f"cannot plan from {req['status']}")
    if req["status"] == REQ_BLOCKED:
        req = transition_request(req, REQ_EVALUATING, reason="replan")
        _save_request(fs, req)

    policy_actions = list(req.get("policy_actions") or [])
    if len(policy_actions) > MAX_ACTIONS_PER_REQUEST:
        req = transition_request(req, REQ_BLOCKED, reason="max_actions_exceeded")
        _save_request(fs, req)
        _append_evidence(fs, {
            "event": "maintenance_blocked",
            "maintenance_request_id": mid,
            "reason": "max_actions_exceeded",
        })
        return req

    actions = []
    seq = 0
    for pa in policy_actions:
        seq += 1
        ver = pa.get("verification") or {}
        ver_cap = ver.get("capability") if ver.get("required") else None
        # Primary action
        actions.append({
            "maintenance_action_id": "ma_" + uuid.uuid4().hex[:12],
            "maintenance_request_id": mid,
            "action_id": pa["id"],
            "capability_id": pa["capability"],
            "required": bool(pa["required"]),
            "sequence": seq,
            "status": ACT_PENDING,
            "operation_id": None,
            "verification_capability_id": ver_cap,
            "verification_operation_id": None,
            "verification_required": bool(ver.get("required")),
            "verification_ordering": ver.get("ordering") or "after",
            "created_at": _now(),
            "updated_at": _now(),
            "version": 1,
        })
        # Explicit verification step as ordered follow-up when required
        if ver.get("required") and ver_cap:
            seq += 1
            actions.append({
                "maintenance_action_id": "ma_" + uuid.uuid4().hex[:12],
                "maintenance_request_id": mid,
                "action_id": f"{pa['id']}.verify",
                "capability_id": ver_cap,
                "required": True,
                "sequence": seq,
                "status": ACT_PENDING,
                "operation_id": None,
                "verification_capability_id": None,
                "verification_operation_id": None,
                "verification_required": False,
                "is_verification_step": True,
                "verifies_action_id": pa["id"],
                "created_at": _now(),
                "updated_at": _now(),
                "version": 1,
            })

    if seq > MAX_ACTIONS_PER_REQUEST:
        req = transition_request(req, REQ_BLOCKED, reason="max_actions_exceeded")
        _save_request(fs, req)
        return req

    req = dict(req)
    req["actions"] = actions
    req = transition_request(req, REQ_PLANNED, reason="planned")
    _save_request(fs, req)
    return req


def authorize_maintenance_request(
    fs: FileService,
    mid: str,
    *,
    owner_id: str,
    project_id: str,
    granted_capabilities: Optional[set[str]] = None,
) -> dict:
    """Governed authorization — planner cannot set authorized=true."""
    req = load_request(fs, mid)
    if not req:
        raise ProjectMaintainError("NOT_FOUND", mid)
    try:
        require_owner_id(owner_id)
        require_project_id(project_id)
        assert_ownership_match(
            expected_owner=str(req.get("owner_id") or ""),
            actual_owner=owner_id,
            expected_project=str(req.get("project_id") or ""),
            actual_project=project_id,
            resource="maintenance_request",
        )
    except SecurityPolicyError as e:
        _append_evidence(fs, {
            "event": "authorization_rejected",
            "maintenance_request_id": mid,
            "reason": e.code,
            "owner_id": owner_id,
            "project_id": project_id,
        })
        raise _policy(e) from e

    if req["status"] == REQ_PLANNED:
        req = transition_request(req, REQ_AWAITING_AUTHORIZATION, reason="auth_check")
        _save_request(fs, req)

    if req["status"] != REQ_AWAITING_AUTHORIZATION:
        if req["status"] == REQ_AUTHORIZED:
            return req
        raise ProjectMaintainError("INVALID_STATE", f"cannot authorize from {req['status']}")

    grants = set(granted_capabilities or [])
    for act in req.get("actions") or []:
        cap = act.get("capability_id")
        if cap not in ALLOWED_MAINTAIN_CAPS:
            req = transition_request(req, REQ_REJECTED, reason=f"forbidden_capability:{cap}")
            _save_request(fs, req)
            _append_evidence(fs, {
                "event": "authorization_rejected",
                "maintenance_request_id": mid,
                "capability_id": cap,
                "reason": "forbidden_capability",
            })
            return req
        meta = DEFAULT_MAINTENANCE_META.get(cap) or {}
        if not meta.get("can_be_maintenance_action", False):
            req = transition_request(req, REQ_REJECTED, reason=f"not_maintenance_eligible:{cap}")
            _save_request(fs, req)
            return req
        if grants and cap not in grants and CAP_PROJECT_MAINTAIN not in grants:
            req = transition_request(req, REQ_REJECTED, reason=f"not_granted:{cap}")
            _save_request(fs, req)
            _append_evidence(fs, {
                "event": "authorization_rejected",
                "maintenance_request_id": mid,
                "capability_id": cap,
                "reason": "not_granted",
            })
            return req
        if cap == "artifact.delete":
            # Destructive: requires explicit grant of the capability itself
            if grants and "artifact.delete" not in grants:
                req = transition_request(req, REQ_REJECTED, reason="destructive_not_granted")
                _save_request(fs, req)
                return req

    # Authorize actions
    actions = []
    for act in req.get("actions") or []:
        if act["status"] == ACT_PENDING:
            act = transition_action(act, ACT_AUTHORIZED, reason="request_authorized")
        actions.append(act)
    req = dict(req)
    req["actions"] = actions
    req = transition_request(req, REQ_AUTHORIZED, reason="ucip_ok")
    _save_request(fs, req)
    _append_evidence(fs, {
        "event": "authorization_granted",
        "maintenance_request_id": mid,
        "owner_id": owner_id,
        "project_id": project_id,
    })
    return req


def execute_maintenance_action(
    fs: FileService,
    mid: str,
    action_id: str,
    *,
    operation_result: str = "SUCCEEDED",
    operation_id: Optional[str] = None,
) -> dict:
    """
    Execute one action's primary operation.

    operation_result is the *operation* domain outcome from the capability substrate
    (SUCCEEDED/FAILED/UNKNOWN) — never inferred from observation health.
    """
    req = load_request(fs, mid)
    if not req:
        raise ProjectMaintainError("NOT_FOUND", mid)
    if req["status"] == REQ_AUTHORIZED:
        req = transition_request(req, REQ_EXECUTING, reason="start_execution")
        _save_request(fs, req)
    if req["status"] not in (REQ_EXECUTING, REQ_VERIFYING):
        raise ProjectMaintainError("INVALID_STATE", f"cannot execute from {req['status']}")

    actions = list(req.get("actions") or [])
    found = None
    for i, act in enumerate(actions):
        if act.get("action_id") == action_id or act.get("maintenance_action_id") == action_id:
            found = i
            break
    if found is None:
        raise ProjectMaintainError("ACTION_NOT_FOUND", action_id)

    act = dict(actions[found])
    if act["status"] == ACT_AUTHORIZED:
        act = transition_action(act, ACT_EXECUTING, reason="start")
    elif act["status"] != ACT_EXECUTING:
        raise ProjectMaintainError("INVALID_STATE", f"action cannot execute from {act['status']}")

    op_id = operation_id or ("op_" + uuid.uuid4().hex[:12])
    act["operation_id"] = op_id
    op = (operation_result or "").upper()

    if op == "UNKNOWN":
        act = transition_action(act, ACT_UNKNOWN, reason="operation_unknown")
        actions[found] = act
        req = dict(req)
        req["actions"] = actions
        req = transition_request(req, REQ_UNKNOWN, reason="action_unknown")
        _save_request(fs, req)
        _append_evidence(fs, {
            "event": "action_unknown",
            "maintenance_request_id": mid,
            "maintenance_action_id": act["maintenance_action_id"],
            "operation_id": op_id,
            "capability_id": act["capability_id"],
            "owner_id": req["owner_id"],
            "project_id": req["project_id"],
            "failure_classification": "UNKNOWN",
        })
        return req

    if op != "SUCCEEDED":
        act = transition_action(act, ACT_FAILED, reason=f"operation_{op.lower()}")
        actions[found] = act
        req = dict(req)
        req["actions"] = actions
        if act.get("required"):
            req = transition_request(req, REQ_FAILED, reason="required_action_failed")
        _save_request(fs, req)
        _append_evidence(fs, {
            "event": "action_failed",
            "maintenance_request_id": mid,
            "maintenance_action_id": act["maintenance_action_id"],
            "operation_id": op_id,
            "capability_id": act["capability_id"],
            "owner_id": req["owner_id"],
            "project_id": req["project_id"],
            "failure_classification": "FAILED",
            "failure_reason": act.get("failure_reason"),
        })
        return req

    # Primary operation succeeded — verification may still be required
    if act.get("verification_required") and act.get("verification_capability_id"):
        act = transition_action(act, ACT_VERIFYING, reason="await_verification")
    else:
        # No verification on this action — succeed only for non-verification-required
        if act.get("is_verification_step"):
            act = transition_action(act, ACT_SUCCEEDED, reason="verification_op_ok")
        else:
            act = transition_action(act, ACT_SUCCEEDED, reason="no_verification_required")
    actions[found] = act
    req = dict(req)
    req["actions"] = actions
    if any(a.get("status") == ACT_VERIFYING for a in actions):
        if req["status"] == REQ_EXECUTING:
            req = transition_request(req, REQ_VERIFYING, reason="verifying")
    _save_request(fs, req)
    return req


def verify_maintenance_action(
    fs: FileService,
    mid: str,
    action_id: str,
    *,
    verification_result: str = "SUCCEEDED",
    verification_operation_id: Optional[str] = None,
) -> dict:
    """Run verification for an action in VERIFYING state."""
    req = load_request(fs, mid)
    if not req:
        raise ProjectMaintainError("NOT_FOUND", mid)
    actions = list(req.get("actions") or [])
    found = None
    for i, act in enumerate(actions):
        if act.get("action_id") == action_id or act.get("maintenance_action_id") == action_id:
            found = i
            break
    if found is None:
        raise ProjectMaintainError("ACTION_NOT_FOUND", action_id)
    act = dict(actions[found])
    if act["status"] != ACT_VERIFYING:
        raise ProjectMaintainError("INVALID_STATE", f"action not VERIFYING: {act['status']}")

    vop = verification_operation_id or ("opv_" + uuid.uuid4().hex[:12])
    act["verification_operation_id"] = vop
    vr = (verification_result or "").upper()

    if vr == "UNKNOWN":
        act = transition_action(act, ACT_UNKNOWN, reason="verification_unknown")
        actions[found] = act
        req = dict(req)
        req["actions"] = actions
        req = transition_request(req, REQ_UNKNOWN, reason="verification_unknown")
        _save_request(fs, req)
        return req
    if vr != "SUCCEEDED":
        act = transition_action(act, ACT_FAILED, reason="verification_failed")
        actions[found] = act
        req = dict(req)
        req["actions"] = actions
        if act.get("required"):
            req = transition_request(req, REQ_FAILED, reason="required_verification_failed")
        _save_request(fs, req)
        _append_evidence(fs, {
            "event": "verification_failed",
            "maintenance_request_id": mid,
            "maintenance_action_id": act["maintenance_action_id"],
            "operation_id": act.get("operation_id"),
            "capability_id": act.get("verification_capability_id") or act.get("capability_id"),
            "owner_id": req["owner_id"],
            "project_id": req["project_id"],
            "failure_classification": "FAILED",
        })
        return req

    act = transition_action(act, ACT_SUCCEEDED, reason="verification_ok")
    actions[found] = act
    req = dict(req)
    req["actions"] = actions
    _save_request(fs, req)
    return req


def reconcile_maintenance_request(fs: FileService, mid: str) -> dict:
    """Resolve request if all required actions succeeded; never from observation alone."""
    req = load_request(fs, mid)
    if not req:
        raise ProjectMaintainError("NOT_FOUND", mid)
    if req["status"] in (REQ_RESOLVED, REQ_REJECTED, REQ_CANCELLED, REQ_FAILED):
        return req
    if req["status"] == REQ_UNKNOWN:
        raise ProjectMaintainError("UNKNOWN_BLOCKED", "cannot resolve while UNKNOWN")

    actions = list(req.get("actions") or [])
    if not actions:
        raise ProjectMaintainError("NO_ACTIONS", "no planned actions")

    required = [a for a in actions if a.get("required")]
    for a in required:
        st = a.get("status")
        if st == ACT_FAILED:
            if req["status"] not in (REQ_FAILED,):
                req = transition_request(req, REQ_FAILED, reason="required_action_failed")
                _save_request(fs, req)
            return req
        if st == ACT_UNKNOWN:
            if req["status"] != REQ_UNKNOWN:
                req = transition_request(req, REQ_UNKNOWN, reason="required_action_unknown")
                _save_request(fs, req)
            return req
        if st == ACT_BLOCKED:
            if req["status"] != REQ_BLOCKED:
                req = transition_request(req, REQ_BLOCKED, reason="required_action_blocked")
                _save_request(fs, req)
            return req
        if st != ACT_SUCCEEDED:
            return req  # still in progress

    # All required succeeded (optional may have FAILED/SKIPPED/UNKNOWN)
    if req["status"] in (REQ_EXECUTING, REQ_VERIFYING, REQ_AUTHORIZED):
        if req["status"] == REQ_EXECUTING:
            req = transition_request(req, REQ_VERIFYING, reason="reconcile")
            _save_request(fs, req)
        req = transition_request(req, REQ_RESOLVED, reason="required_actions_succeeded")
        _save_request(fs, req)
        _append_evidence(fs, {
            "event": "maintenance_resolved",
            "maintenance_request_id": mid,
            "owner_id": req["owner_id"],
            "project_id": req["project_id"],
        })
    return req


def cancel_maintenance_request(
    fs: FileService,
    mid: str,
    *,
    actor: str = "owner",
    reason: str = "cancelled",
) -> dict:
    req = load_request(fs, mid)
    if not req:
        raise ProjectMaintainError("NOT_FOUND", mid)
    prev = req["status"]
    if prev in (REQ_RESOLVED, REQ_REJECTED, REQ_CANCELLED, REQ_FAILED):
        raise ProjectMaintainError("INVALID_TRANSITION", f"cannot cancel terminal {prev}")
    if not _can_transition(REQUEST_TRANSITIONS, prev, REQ_CANCELLED):
        raise ProjectMaintainError("INVALID_TRANSITION", f"cannot cancel from {prev}")

    actions = []
    for act in req.get("actions") or []:
        if act["status"] in (ACT_SUCCEEDED, ACT_FAILED, ACT_CANCELLED, ACT_SKIPPED, ACT_UNKNOWN):
            actions.append(act)
        elif _can_transition(ACTION_TRANSITIONS, act["status"], ACT_CANCELLED):
            actions.append(transition_action(act, ACT_CANCELLED, reason=reason))
        else:
            actions.append(act)
    req = dict(req)
    req["actions"] = actions
    req = transition_request(req, REQ_CANCELLED, reason=reason)
    _save_request(fs, req)
    _append_evidence(fs, {
        "event": "maintenance_cancelled",
        "maintenance_request_id": mid,
        "previous_status": prev,
        "new_status": REQ_CANCELLED,
        "actor": actor,
        "reason": reason,
    })
    return req


def trigger_from_observation(
    fs: FileService,
    *,
    owner_id: str,
    project_id: str,
    deployment_id: str,
    observation_id: str,
    observation_kind: str,
    observation_status: str,
) -> dict:
    """
    Observation trigger → may create DETECTED request.
    Does NOT authorize or execute maintenance.
    """
    return create_maintenance_request(
        fs,
        owner_id=owner_id,
        project_id=project_id,
        deployment_id=deployment_id,
        source_observation_id=observation_id,
        observation_kind=observation_kind,
        observation_status=observation_status,
    )


async def execute_project_maintain(contract, req) -> dict:
    """Capability substrate entry for project.maintain."""
    try:
        reject_planner_forbidden_fields(
            req.inputs if hasattr(req, "inputs") else {},
            extra_forbidden=(
                "owner_id", "project_id", "deployment_id", "source_observation_id",
                "authorized", "authorization", "command", "shell", "argv",
                "url", "host", "port", "path", "credentials", "environment",
                "health", "observation_status", "operation_status",
            ),
        )
    except SecurityPolicyError as e:
        raise RuntimeError(f"{e.code}:{e.message}") from e

    ctx = getattr(req, "context", None)
    owner = getattr(ctx, "owner_id", None) if ctx else None
    meta = getattr(ctx, "metadata", None) or {}
    project_id = meta.get("project_id") if isinstance(meta, dict) else None
    try:
        owner = require_owner_id(owner)
        project_id = require_project_id(project_id)
    except SecurityPolicyError as e:
        raise RuntimeError(f"{e.code}:{e.message}") from e

    inputs = dict(getattr(req, "inputs", None) or {})
    profile = inputs.get("profile") or PROFILE_MAINTAIN
    if profile != PROFILE_MAINTAIN:
        raise RuntimeError("PROFILE_INVALID:profile must be maintain")

    fs = FileService(owner, project_id)
    action = str(inputs.get("action") or "status")

    if action == "ensure_contract":
        data = write_default_maintain_contract(fs)
        return {"success": True, "contract": data, "status": "succeeded"}

    if action == "trigger":
        # Load trusted observation record from workspace — never planner-forged identity
        from execution.project_observe import OBSERVE_RECORD
        trusted = _read_json(fs, OBSERVE_RECORD) or {}
        mid_req = trigger_from_observation(
            fs,
            owner_id=owner,
            project_id=project_id,
            deployment_id=str(trusted.get("deployment_id") or ""),
            observation_id=str(trusted.get("observation_id") or ""),
            observation_kind=str(
                (trusted.get("primary_kind") or trusted.get("kind") or "health")
            ),
            observation_status=str(
                trusted.get("status") or trusted.get("observation_status") or ""
            ),
        )
        return {
            "success": True,
            "status": "succeeded",
            "maintenance_request": mid_req,
            "operation_status": "SUCCEEDED",
            "note": "trigger_does_not_authorize",
        }

    mid = str(inputs.get("maintenance_request_id") or "")
    if not mid:
        raise RuntimeError("MAINTENANCE_ID_REQUIRED:maintenance_request_id required")

    if action == "evaluate":
        out = evaluate_maintenance_request(fs, mid)
    elif action == "plan":
        out = plan_maintenance_request(fs, mid)
    elif action == "authorize":
        grants = set(getattr(ctx, "granted_capabilities", None) or [])
        out = authorize_maintenance_request(
            fs, mid, owner_id=owner, project_id=project_id, granted_capabilities=grants,
        )
    elif action == "execute_action":
        out = execute_maintenance_action(
            fs, mid,
            str(inputs.get("action_id") or ""),
            operation_result=str(inputs.get("operation_result") or "SUCCEEDED"),
            operation_id=inputs.get("operation_id"),
        )
    elif action == "verify_action":
        out = verify_maintenance_action(
            fs, mid,
            str(inputs.get("action_id") or ""),
            verification_result=str(inputs.get("verification_result") or "SUCCEEDED"),
            verification_operation_id=inputs.get("verification_operation_id"),
        )
    elif action == "reconcile":
        out = reconcile_maintenance_request(fs, mid)
    elif action == "cancel":
        out = cancel_maintenance_request(fs, mid, actor=owner, reason=str(inputs.get("reason") or "cancelled"))
    elif action == "status":
        out = load_request(fs, mid) or {}
    else:
        raise RuntimeError(f"UNKNOWN_ACTION:{action}")

    return {
        "success": True,
        "status": "succeeded",
        "operation_status": "SUCCEEDED",
        "maintenance_request": out,
        "maintenance_request_status": out.get("status"),
        "note": "maintenance_request_status_independent_of_task_and_observation",
    }


def _make_executor():
    async def _exec(contract, req):
        try:
            return await execute_project_maintain(contract, req)
        except ProjectMaintainError as e:
            raise RuntimeError(f"{e.code}:{e.message}") from e
        except PathViolation as e:
            raise RuntimeError(f"PATH_VIOLATION:{e}") from e

    return _exec


def ensure_project_maintain_registered() -> None:
    from governance.capability_substrate import get_capability_substrate
    from governance.capability_catalog import (
        CapabilityCatalogEntry,
        catalog_register_entry_for_tests,
    )

    sub = get_capability_substrate()
    sub.register_executor(CAP_PROJECT_MAINTAIN, _make_executor())
    schema = {
        "type": "object",
        "required": ["profile"],
        "properties": {
            "profile": {"type": "string", "description": "Allowlisted: maintain"},
            "action": {"type": "string"},
            "maintenance_request_id": {"type": "string"},
            "action_id": {"type": "string"},
            "observation": {"type": "object"},
            "operation_result": {"type": "string"},
            "verification_result": {"type": "string"},
        },
        "additionalProperties": False,
    }
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id=CAP_PROJECT_MAINTAIN,
            display_name="Project Maintain",
            description=(
                "Governed maintenance orchestration. "
                "Does not execute shell; invokes catalogued capabilities only. "
                "Maintenance request status ≠ observation health ≠ task completion."
            ),
            version="1",
            input_schema=schema,
            output_schema={
                "type": "object",
                "required": ["success", "maintenance_request_status"],
                "properties": {
                    "success": {"type": "boolean"},
                    "maintenance_request_status": {"type": "string"},
                    "maintenance_request": {"type": "object"},
                },
            },
            risk_class="privileged",
            consequential=True,
            requires_authorization=True,
            required_isolation="default",
            evidence_requirements={"maintenance_request_id": True},
            status="active",
            category="maintain",
            source="project_maintain",
            maintenance={
                "can_be_maintenance_action": False,
                "can_verify_maintenance": False,
                "verification_scope": "orchestration",
            },
        )
    )
    # Annotate peer capabilities with maintenance metadata for catalog consumers
    for cap_id, meta in DEFAULT_MAINTENANCE_META.items():
        try:
            entry = CapabilityCatalogEntry(
                capability_id=cap_id,
                display_name=cap_id,
                description=f"Maintenance metadata for {cap_id}",
                version="1",
                risk_class="write",
                consequential=True,
                requires_authorization=True,
                status="active",
                category="maintain_meta",
                source="project_maintain",
                maintenance=meta,
            )
            catalog_register_entry_for_tests(entry)
        except Exception:
            pass
