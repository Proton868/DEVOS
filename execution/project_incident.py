"""
Governed Incident domain foundation.

Sixth independent state domain:

  Task / Operation / Observation / Maintenance Request / Maintenance Action / Incident

«An observation may reveal an incident. An incident may require maintenance.
 Maintenance may invoke governed capabilities. But no domain may impersonate
 another domain's authority or lifecycle.»

Incident does NOT:
  - auto-remediate
  - page / alert
  - execute shell/network/infra
  - resolve from task/operation success alone
  - mutate observation health

Mitigation flows only through existing Maintain.
"""
from __future__ import annotations

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

logger = logging.getLogger("devos.project_incident")

CAP_PROJECT_INCIDENT = "project.incident"
PROFILE_INCIDENT = "incident"
INCIDENT_CONTRACT = "devos.incident.json"
INCIDENT_DIR = ".devos/incidents"

# ── Lifecycle ─────────────────────────────────────────────────────────────────
INC_DETECTED = "DETECTED"
INC_OPEN = "OPEN"
INC_ASSESSING = "ASSESSING"
INC_ACKNOWLEDGED = "ACKNOWLEDGED"
INC_MITIGATING = "MITIGATING"
INC_VERIFYING = "VERIFYING"
INC_RESOLVED = "RESOLVED"
INC_REJECTED = "REJECTED"
INC_CANCELLED = "CANCELLED"
INC_DUPLICATE = "DUPLICATE"
INC_BLOCKED = "BLOCKED"
INC_FAILED = "FAILED"
INC_UNKNOWN = "UNKNOWN"

INCIDENT_TRANSITIONS: dict[str, frozenset[str]] = {
    INC_DETECTED: frozenset({INC_OPEN, INC_REJECTED, INC_DUPLICATE}),
    INC_OPEN: frozenset({INC_ASSESSING, INC_ACKNOWLEDGED, INC_REJECTED, INC_DUPLICATE, INC_CANCELLED}),
    INC_ASSESSING: frozenset({INC_ACKNOWLEDGED, INC_BLOCKED, INC_REJECTED, INC_CANCELLED}),
    INC_ACKNOWLEDGED: frozenset({INC_MITIGATING, INC_VERIFYING, INC_BLOCKED, INC_CANCELLED}),
    INC_MITIGATING: frozenset({INC_VERIFYING, INC_FAILED, INC_BLOCKED, INC_UNKNOWN, INC_CANCELLED}),
    INC_VERIFYING: frozenset({INC_RESOLVED, INC_MITIGATING, INC_FAILED, INC_BLOCKED, INC_UNKNOWN}),
    INC_RESOLVED: frozenset(),
    INC_REJECTED: frozenset(),
    INC_DUPLICATE: frozenset(),
    INC_CANCELLED: frozenset(),
    INC_BLOCKED: frozenset({INC_ASSESSING, INC_CANCELLED}),
    INC_FAILED: frozenset(),
    INC_UNKNOWN: frozenset(),  # explicit reconciliation only
}

SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
TRIGGER_KINDS = frozenset({
    "runtime_state", "readiness", "health", "artifact_identity", "deployment_identity",
})
OBS_STATUSES = frozenset({
    "OBSERVED", "UNHEALTHY", "UNREADY", "DEGRADED", "UNAVAILABLE", "FAILED", "HEALTHY",
})

POLICY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_LOCK = threading.RLock()
_EVIDENCE: list[dict] = []


class ProjectIncidentError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _policy(e: SecurityPolicyError) -> ProjectIncidentError:
    return ProjectIncidentError(e.code, e.message)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def reset_incident_store_for_tests() -> None:
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


def _inc_path(iid: str) -> str:
    return f"{INCIDENT_DIR}/records/{iid}.json"


def _idx_path() -> str:
    return f"{INCIDENT_DIR}/index.json"


def _evidence_path() -> str:
    return f"{INCIDENT_DIR}/evidence.jsonl"


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
        logger.debug("incident evidence append failed: %s", type(e).__name__)


def list_incident_evidence() -> list[dict]:
    with _LOCK:
        return list(_EVIDENCE)


# ── Contract ──────────────────────────────────────────────────────────────────

def validate_incident_contract(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ProjectIncidentError("CONTRACT_INVALID", "contract must be object")
    allowed_top = {"version", "profile", "policies"}
    unknown = set(data.keys()) - allowed_top
    if unknown:
        raise ProjectIncidentError("CONTRACT_UNKNOWN_FIELD", f"unknown fields: {sorted(unknown)}")
    if data.get("version") != 1:
        raise ProjectIncidentError("CONTRACT_VERSION", "version must be 1")
    if data.get("profile") != PROFILE_INCIDENT:
        raise ProjectIncidentError("CONTRACT_PROFILE", "profile must be incident")
    policies = data.get("policies")
    if not isinstance(policies, list) or not policies:
        raise ProjectIncidentError("CONTRACT_POLICIES", "policies must be non-empty list")
    seen: set[str] = set()
    for pol in policies:
        if not isinstance(pol, dict):
            raise ProjectIncidentError("CONTRACT_POLICY", "policy must be object")
        p_allowed = {"id", "trigger", "severity", "requires_acknowledgement"}
        if set(pol.keys()) - p_allowed:
            raise ProjectIncidentError("CONTRACT_UNKNOWN_FIELD", "policy has unknown fields")
        pid = str(pol.get("id") or "")
        if not POLICY_ID_RE.match(pid):
            raise ProjectIncidentError("CONTRACT_POLICY_ID", f"invalid policy id: {pid}")
        if pid in seen:
            raise ProjectIncidentError("CONTRACT_DUPLICATE_ID", f"duplicate policy id: {pid}")
        seen.add(pid)
        sev = str(pol.get("severity") or "")
        if sev not in SEVERITIES:
            raise ProjectIncidentError("CONTRACT_SEVERITY", f"invalid severity: {sev}")
        if "requires_acknowledgement" not in pol or not isinstance(pol["requires_acknowledgement"], bool):
            raise ProjectIncidentError("CONTRACT_ACK", "requires_acknowledgement must be boolean")
        trig = pol.get("trigger")
        if not isinstance(trig, dict):
            raise ProjectIncidentError("CONTRACT_TRIGGER", "trigger must be object")
        t_allowed = {"source", "kind", "status"}
        if set(trig.keys()) - t_allowed:
            raise ProjectIncidentError("CONTRACT_TRIGGER", "invalid trigger fields")
        if trig.get("source") != "observation":
            raise ProjectIncidentError("CONTRACT_TRIGGER", "trigger.source must be observation")
        if trig.get("kind") not in TRIGGER_KINDS:
            raise ProjectIncidentError("CONTRACT_TRIGGER", f"invalid trigger.kind: {trig.get('kind')}")
        if trig.get("status") not in OBS_STATUSES:
            raise ProjectIncidentError("CONTRACT_TRIGGER", f"invalid trigger.status: {trig.get('status')}")
    return data


def load_and_validate_incident_contract(fs: FileService) -> dict:
    data = _read_json(fs, INCIDENT_CONTRACT)
    if not data:
        raise ProjectIncidentError("CONTRACT_MISSING", f"{INCIDENT_CONTRACT} not found")
    return validate_incident_contract(data)


def write_default_incident_contract(fs: FileService) -> dict:
    data = {
        "version": 1,
        "profile": "incident",
        "policies": [
            {
                "id": "deployment-unhealthy",
                "trigger": {
                    "source": "observation",
                    "kind": "health",
                    "status": "UNHEALTHY",
                },
                "severity": "HIGH",
                "requires_acknowledgement": True,
            }
        ],
    }
    validate_incident_contract(data)
    _write_json(fs, INCIDENT_CONTRACT, data)
    return data


# ── Transitions ───────────────────────────────────────────────────────────────

def _can_transition(current: str, nxt: str) -> bool:
    return nxt in INCIDENT_TRANSITIONS.get(current, frozenset())


def transition_incident(inc: dict, new_status: str, *, reason: str = "", actor: str = "system") -> dict:
    cur = inc.get("status")
    if not _can_transition(cur, new_status):
        raise ProjectIncidentError(
            "INVALID_TRANSITION",
            f"incident cannot transition {cur} → {new_status}",
        )
    prev = cur
    inc = dict(inc)
    inc["status"] = new_status
    inc["updated_at"] = _now()
    inc["version"] = int(inc.get("version") or 0) + 1
    if reason:
        inc["last_transition_reason"] = reason
    if new_status == INC_ACKNOWLEDGED:
        inc["acknowledged_at"] = _now()
        inc["acknowledged_by"] = actor
    if new_status == INC_RESOLVED:
        inc["resolved_at"] = _now()
    if new_status == INC_FAILED and reason:
        inc["failure_reason"] = reason
    if new_status == INC_BLOCKED and reason:
        inc["blocked_reason"] = reason
    if new_status == INC_UNKNOWN and reason:
        inc["unknown_reason"] = reason
    inc["_transition_meta"] = {
        "previous_status": prev,
        "new_status": new_status,
        "actor": actor,
        "reason": reason,
    }
    return inc


def _correlation_key(
    *,
    owner_id: str,
    project_id: str,
    deployment_id: str,
    policy_id: str,
) -> str:
    """Deterministic correlation identity (no fuzzy AI)."""
    return f"{owner_id}|{project_id}|{deployment_id}|{policy_id}"


# ── Persistence ───────────────────────────────────────────────────────────────

def _save_incident(fs: FileService, inc: dict) -> dict:
    iid = inc["incident_id"]
    meta = inc.pop("_transition_meta", None)
    with _LOCK:
        existing = _read_json(fs, _inc_path(iid))
        if existing and int(existing.get("version") or 0) > int(inc.get("version") or 0):
            raise ProjectIncidentError("CAS_CONFLICT", "concurrent update rejected")
        _write_json(fs, _inc_path(iid), inc)
        idx = _read_json(fs, _idx_path()) or {"incidents": [], "active_by_correlation": {}}
        ids = list(idx.get("incidents") or [])
        if iid not in ids:
            ids.append(iid)
        idx["incidents"] = ids
        corr = inc.get("correlation_key")
        if corr and inc.get("status") not in (
            INC_RESOLVED, INC_REJECTED, INC_CANCELLED, INC_DUPLICATE, INC_FAILED,
        ):
            active = dict(idx.get("active_by_correlation") or {})
            active[corr] = iid
            idx["active_by_correlation"] = active
        elif corr:
            active = dict(idx.get("active_by_correlation") or {})
            if active.get(corr) == iid:
                active.pop(corr, None)
            idx["active_by_correlation"] = active
        _write_json(fs, _idx_path(), idx)
    if meta:
        _append_evidence(fs, {
            "event": "incident_transition",
            "incident_id": iid,
            "previous_status": meta.get("previous_status"),
            "new_status": meta.get("new_status"),
            "actor": meta.get("actor"),
            "reason": meta.get("reason"),
            "source_observation_id": inc.get("source_observation_id"),
            "resolution_observation_id": inc.get("resolution_observation_id"),
            "owner_id": inc.get("owner_id"),
            "project_id": inc.get("project_id"),
        })
    return inc


def load_incident(fs: FileService, iid: str) -> Optional[dict]:
    return _read_json(fs, _inc_path(iid))


def find_active_by_correlation(fs: FileService, correlation_key: str) -> Optional[dict]:
    idx = _read_json(fs, _idx_path()) or {}
    iid = (idx.get("active_by_correlation") or {}).get(correlation_key)
    if not iid:
        return None
    inc = load_incident(fs, iid)
    if not inc:
        return None
    if inc.get("status") in (
        INC_RESOLVED, INC_REJECTED, INC_CANCELLED, INC_DUPLICATE, INC_FAILED,
    ):
        return None
    return inc


# ── Core API ──────────────────────────────────────────────────────────────────

def detect_incident(
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
    """
    Match observation against incident policy → DETECTED (or DUPLICATE correlation).

    Does NOT authorize maintenance. Does NOT auto-remediate.
    Without a matching policy, raises — observation alone is not an incident.
    """
    try:
        require_owner_id(owner_id)
        require_project_id(project_id)
    except SecurityPolicyError as e:
        raise _policy(e) from e
    if not deployment_id or not source_observation_id:
        raise ProjectIncidentError("IDENTITY_REQUIRED", "deployment_id and source_observation_id required")
    if observation_kind not in TRIGGER_KINDS:
        raise ProjectIncidentError("TRIGGER_KIND", f"invalid observation kind: {observation_kind}")
    if observation_status not in OBS_STATUSES:
        raise ProjectIncidentError("TRIGGER_STATUS", f"invalid observation status: {observation_status}")

    try:
        contract = load_and_validate_incident_contract(fs)
    except ProjectIncidentError:
        contract = write_default_incident_contract(fs)

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
        raise ProjectIncidentError(
            "NO_POLICY_MATCH",
            f"no incident policy for observation {observation_kind}/{observation_status}",
        )

    corr = _correlation_key(
        owner_id=owner_id,
        project_id=project_id,
        deployment_id=deployment_id,
        policy_id=matched["id"],
    )
    existing = find_active_by_correlation(fs, corr)
    if existing:
        _append_evidence(fs, {
            "event": "incident_correlated",
            "incident_id": existing["incident_id"],
            "source_observation_id": source_observation_id,
            "correlation_key": corr,
            "owner_id": owner_id,
            "project_id": project_id,
            "note": "duplicate_detection_updates_existing",
        })
        # Record correlation; do not create uncontrolled duplicate
        existing = dict(existing)
        existing["last_correlated_observation_id"] = source_observation_id
        existing["updated_at"] = _now()
        existing["version"] = int(existing.get("version") or 0) + 1
        _save_incident(fs, existing)
        return existing

    return create_incident(
        fs,
        owner_id=owner_id,
        project_id=project_id,
        deployment_id=deployment_id,
        source_observation_id=source_observation_id,
        policy=matched,
        correlation_key=corr,
    )


def create_incident(
    fs: FileService,
    *,
    owner_id: str,
    project_id: str,
    deployment_id: str,
    source_observation_id: str,
    policy: dict,
    correlation_key: str,
) -> dict:
    iid = "inc_" + uuid.uuid4().hex[:16]
    now = _now()
    inc = {
        "incident_id": iid,
        "owner_id": owner_id,
        "project_id": project_id,
        "deployment_id": deployment_id,
        "source_observation_id": source_observation_id,
        "policy_id": policy["id"],
        "policy_version": 1,
        "severity": policy["severity"],
        "requires_acknowledgement": bool(policy.get("requires_acknowledgement")),
        "status": INC_DETECTED,
        "created_at": now,
        "updated_at": now,
        "version": 1,
        "correlation_key": correlation_key,
        "maintenance_request_id": None,
        "summary": f"Incident from observation {source_observation_id}",
        "trigger": dict(policy.get("trigger") or {}),
    }
    _save_incident(fs, inc)
    _append_evidence(fs, {
        "event": "incident_detected",
        "incident_id": iid,
        "previous_status": None,
        "new_status": INC_DETECTED,
        "actor": "system",
        "reason": "policy_match",
        "source_observation_id": source_observation_id,
        "owner_id": owner_id,
        "project_id": project_id,
        "severity": policy["severity"],
    })
    return inc


def assess_incident(fs: FileService, iid: str) -> dict:
    inc = load_incident(fs, iid)
    if not inc:
        raise ProjectIncidentError("NOT_FOUND", iid)
    if inc["status"] == INC_DETECTED:
        inc = transition_incident(inc, INC_OPEN, reason="open")
        _save_incident(fs, inc)
    if inc["status"] == INC_OPEN:
        inc = transition_incident(inc, INC_ASSESSING, reason="assess")
        _save_incident(fs, inc)
    elif inc["status"] == INC_BLOCKED:
        inc = transition_incident(inc, INC_ASSESSING, reason="reassess")
        _save_incident(fs, inc)
    return inc


def acknowledge_incident(
    fs: FileService,
    iid: str,
    *,
    actor: str,
    owner_id: str,
    project_id: str,
) -> dict:
    """ACKNOWLEDGED ≠ AUTHORIZED and ≠ RESOLVED."""
    inc = load_incident(fs, iid)
    if not inc:
        raise ProjectIncidentError("NOT_FOUND", iid)
    try:
        require_owner_id(owner_id)
        require_project_id(project_id)
        assert_ownership_match(
            expected_owner=str(inc.get("owner_id") or ""),
            actual_owner=owner_id,
            expected_project=str(inc.get("project_id") or ""),
            actual_project=project_id,
            resource="incident",
        )
    except SecurityPolicyError as e:
        raise _policy(e) from e

    if inc["status"] == INC_DETECTED:
        inc = transition_incident(inc, INC_OPEN, reason="open_for_ack", actor=actor)
        _save_incident(fs, inc)
    if inc["status"] == INC_OPEN:
        # May go OPEN → ACKNOWLEDGED directly, or via ASSESSING
        if inc.get("requires_acknowledgement"):
            # Prefer assess path when policy requires formal ack after assess
            inc = transition_incident(inc, INC_ASSESSING, reason="pre_ack_assess", actor=actor)
            _save_incident(fs, inc)
    if inc["status"] == INC_ASSESSING:
        inc = transition_incident(inc, INC_ACKNOWLEDGED, reason="acknowledged", actor=actor)
        _save_incident(fs, inc)
    elif inc["status"] == INC_OPEN:
        inc = transition_incident(inc, INC_ACKNOWLEDGED, reason="acknowledged", actor=actor)
        _save_incident(fs, inc)
    elif inc["status"] == INC_ACKNOWLEDGED:
        return inc
    else:
        raise ProjectIncidentError("INVALID_STATE", f"cannot acknowledge from {inc['status']}")
    return inc


def link_maintenance_request(
    fs: FileService,
    iid: str,
    *,
    maintenance_request_id: str,
    owner_id: str,
    project_id: str,
) -> dict:
    """
    Attach existing Maintain request. Does not copy maintenance state into incident.
    Does not authorize maintenance.
    """
    inc = load_incident(fs, iid)
    if not inc:
        raise ProjectIncidentError("NOT_FOUND", iid)
    try:
        assert_ownership_match(
            expected_owner=str(inc.get("owner_id") or ""),
            actual_owner=owner_id,
            expected_project=str(inc.get("project_id") or ""),
            actual_project=project_id,
            resource="incident",
        )
    except SecurityPolicyError as e:
        raise _policy(e) from e
    if not maintenance_request_id:
        raise ProjectIncidentError("MR_REQUIRED", "maintenance_request_id required")
    if inc["status"] not in (INC_ACKNOWLEDGED, INC_MITIGATING, INC_ASSESSING):
        # From ACKNOWLEDGED we move to MITIGATING when linking work
        if inc["status"] != INC_ACKNOWLEDGED:
            raise ProjectIncidentError("INVALID_STATE", f"cannot link maintenance from {inc['status']}")

    inc = dict(inc)
    inc["maintenance_request_id"] = maintenance_request_id
    inc["updated_at"] = _now()
    inc["version"] = int(inc.get("version") or 0) + 1
    if inc["status"] == INC_ACKNOWLEDGED:
        inc = transition_incident(inc, INC_MITIGATING, reason="maintenance_linked", actor=owner_id)
    _save_incident(fs, inc)
    _append_evidence(fs, {
        "event": "incident_maintenance_linked",
        "incident_id": iid,
        "maintenance_request_id": maintenance_request_id,
        "owner_id": owner_id,
        "project_id": project_id,
        "note": "incident_does_not_own_maintenance_state",
    })
    return inc


def verify_incident(
    fs: FileService,
    iid: str,
    *,
    resolution_observation_id: str,
    observation_status: str,
    maintenance_request_status: Optional[str] = None,
) -> dict:
    """
    Enter VERIFYING with trusted observation identity.
    HEALTHY observation alone does not RESOLVE — resolve_incident applies criteria.
    """
    inc = load_incident(fs, iid)
    if not inc:
        raise ProjectIncidentError("NOT_FOUND", iid)
    if not resolution_observation_id:
        raise ProjectIncidentError("OBS_REQUIRED", "resolution_observation_id required")

    if maintenance_request_status == "UNKNOWN":
        # Required maintenance uncertainty blocks incident resolution path
        if inc["status"] in (INC_MITIGATING, INC_ACKNOWLEDGED, INC_VERIFYING):
            if _can_transition(inc["status"], INC_UNKNOWN):
                inc = transition_incident(inc, INC_UNKNOWN, reason="maintenance_unknown")
                _save_incident(fs, inc)
            else:
                inc = dict(inc)
                inc["unknown_reason"] = "maintenance_unknown"
                inc["updated_at"] = _now()
                _save_incident(fs, inc)
        return inc

    if inc["status"] == INC_MITIGATING:
        inc = transition_incident(inc, INC_VERIFYING, reason="begin_verification")
        _save_incident(fs, inc)
    elif inc["status"] == INC_ACKNOWLEDGED:
        # Policy allows ACKNOWLEDGED → VERIFYING (no maintenance path)
        inc = transition_incident(inc, INC_VERIFYING, reason="begin_verification")
        _save_incident(fs, inc)
    elif inc["status"] != INC_VERIFYING:
        raise ProjectIncidentError("INVALID_STATE", f"cannot verify from {inc['status']}")

    inc = dict(inc)
    inc["resolution_observation_id"] = resolution_observation_id
    inc["last_verification_observation_status"] = observation_status
    inc["updated_at"] = _now()
    inc["version"] = int(inc.get("version") or 0) + 1
    _save_incident(fs, inc)
    _append_evidence(fs, {
        "event": "incident_verification",
        "incident_id": iid,
        "resolution_observation_id": resolution_observation_id,
        "observation_status": observation_status,
        "note": "healthy_alone_does_not_resolve",
    })
    return inc


def resolve_incident(
    fs: FileService,
    iid: str,
    *,
    resolution_observation_id: Optional[str] = None,
    observation_status: Optional[str] = None,
    maintenance_request_status: Optional[str] = None,
) -> dict:
    """
    Resolve only when explicit criteria satisfied:
      - status is VERIFYING
      - resolution_observation_id present (trusted)
      - observation_status is HEALTHY (or OBSERVED when policy uses that)
      - if maintenance was linked, it must not be UNKNOWN / must be RESOLVED when present
    Does not mutate observation records.
    """
    inc = load_incident(fs, iid)
    if not inc:
        raise ProjectIncidentError("NOT_FOUND", iid)
    if inc["status"] == INC_UNKNOWN:
        raise ProjectIncidentError("UNKNOWN_BLOCKED", "cannot resolve while UNKNOWN")
    if inc["status"] != INC_VERIFYING:
        raise ProjectIncidentError("INVALID_STATE", f"cannot resolve from {inc['status']}")

    robs = resolution_observation_id or inc.get("resolution_observation_id")
    if not robs:
        raise ProjectIncidentError("RESOLUTION_EVIDENCE", "resolution_observation_id required")
    ost = observation_status or inc.get("last_verification_observation_status")
    if ost not in ("HEALTHY", "OBSERVED"):
        raise ProjectIncidentError(
            "RESOLUTION_CRITERIA",
            f"observation status {ost!r} does not satisfy resolution criteria",
        )
    if maintenance_request_status == "UNKNOWN":
        raise ProjectIncidentError("MAINTENANCE_UNKNOWN", "cannot resolve while maintenance is UNKNOWN")
    if inc.get("maintenance_request_id") and maintenance_request_status not in (
        None, "RESOLVED",
    ):
        # Linked maintenance must be resolved before incident resolution when status provided
        if maintenance_request_status in ("FAILED", "CANCELLED", "BLOCKED"):
            raise ProjectIncidentError(
                "MAINTENANCE_NOT_RESOLVED",
                f"linked maintenance status {maintenance_request_status}",
            )

    # Maintenance RESOLVED alone is insufficient without observation criteria (already checked)
    inc = dict(inc)
    inc["resolution_observation_id"] = robs
    inc = transition_incident(inc, INC_RESOLVED, reason="criteria_satisfied")
    _save_incident(fs, inc)
    _append_evidence(fs, {
        "event": "incident_resolved",
        "incident_id": iid,
        "resolution_observation_id": robs,
        "observation_status": ost,
        "maintenance_request_id": inc.get("maintenance_request_id"),
        "maintenance_request_status": maintenance_request_status,
    })
    return inc


def cancel_incident(
    fs: FileService,
    iid: str,
    *,
    actor: str = "owner",
    reason: str = "cancelled",
) -> dict:
    inc = load_incident(fs, iid)
    if not inc:
        raise ProjectIncidentError("NOT_FOUND", iid)
    prev = inc["status"]
    if not _can_transition(prev, INC_CANCELLED):
        raise ProjectIncidentError("INVALID_TRANSITION", f"cannot cancel from {prev}")
    inc = transition_incident(inc, INC_CANCELLED, reason=reason, actor=actor)
    _save_incident(fs, inc)
    _append_evidence(fs, {
        "event": "incident_cancelled",
        "incident_id": iid,
        "previous_status": prev,
        "new_status": INC_CANCELLED,
        "actor": actor,
        "reason": reason,
    })
    return inc


def reconcile_incident(fs: FileService, iid: str) -> dict:
    """Explicit reconciliation entry for UNKNOWN only — no silent success."""
    inc = load_incident(fs, iid)
    if not inc:
        raise ProjectIncidentError("NOT_FOUND", iid)
    if inc["status"] != INC_UNKNOWN:
        return inc
    raise ProjectIncidentError(
        "RECONCILE_REQUIRES_EVIDENCE",
        "UNKNOWN incidents require trusted evidence before any transition",
    )


async def execute_project_incident(contract, req) -> dict:
    """Capability substrate entry — governance/read-oriented only."""
    try:
        reject_planner_forbidden_fields(
            req.inputs if hasattr(req, "inputs") else {},
            extra_forbidden=(
                "owner_id", "project_id", "deployment_id", "source_observation_id",
                "authorized", "authorization", "command", "shell", "argv",
                "url", "host", "port", "path", "credentials", "environment",
                "health", "observation_status", "operation_status",
                "pagerduty", "slack", "email", "runbook",
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
    profile = inputs.get("profile") or PROFILE_INCIDENT
    if profile != PROFILE_INCIDENT:
        raise RuntimeError("PROFILE_INVALID:profile must be incident")

    fs = FileService(owner, project_id)
    action = str(inputs.get("action") or "status")

    if action == "ensure_contract":
        data = write_default_incident_contract(fs)
        return {"success": True, "contract": data, "status": "succeeded"}

    if action == "detect":
        # Trusted observation from workspace record only
        from execution.project_observe import OBSERVE_RECORD
        trusted = _read_json(fs, OBSERVE_RECORD) or {}
        out = detect_incident(
            fs,
            owner_id=owner,
            project_id=project_id,
            deployment_id=str(trusted.get("deployment_id") or ""),
            source_observation_id=str(trusted.get("observation_id") or ""),
            observation_kind=str(trusted.get("primary_kind") or trusted.get("kind") or "health"),
            observation_status=str(trusted.get("status") or trusted.get("observation_status") or ""),
        )
        return {
            "success": True,
            "status": "succeeded",
            "incident": out,
            "incident_status": out.get("status"),
            "operation_status": "SUCCEEDED",
            "note": "detection_does_not_authorize_or_remediate",
        }

    iid = str(inputs.get("incident_id") or "")
    if not iid:
        raise RuntimeError("INCIDENT_ID_REQUIRED:incident_id required")

    if action == "assess":
        out = assess_incident(fs, iid)
    elif action == "acknowledge":
        out = acknowledge_incident(fs, iid, actor=owner, owner_id=owner, project_id=project_id)
    elif action == "link_maintenance":
        out = link_maintenance_request(
            fs, iid,
            maintenance_request_id=str(inputs.get("maintenance_request_id") or ""),
            owner_id=owner,
            project_id=project_id,
        )
    elif action == "verify":
        out = verify_incident(
            fs, iid,
            resolution_observation_id=str(inputs.get("resolution_observation_id") or ""),
            observation_status=str(inputs.get("verification_observation_status") or ""),
            maintenance_request_status=inputs.get("maintenance_request_status"),
        )
    elif action == "resolve":
        out = resolve_incident(
            fs, iid,
            resolution_observation_id=inputs.get("resolution_observation_id"),
            observation_status=inputs.get("verification_observation_status"),
            maintenance_request_status=inputs.get("maintenance_request_status"),
        )
    elif action == "cancel":
        out = cancel_incident(fs, iid, actor=owner, reason=str(inputs.get("reason") or "cancelled"))
    elif action == "reconcile":
        out = reconcile_incident(fs, iid)
    elif action == "status":
        out = load_incident(fs, iid) or {}
    else:
        raise RuntimeError(f"UNKNOWN_ACTION:{action}")

    return {
        "success": True,
        "status": "succeeded",
        "operation_status": "SUCCEEDED",
        "incident": out,
        "incident_status": out.get("status"),
        "note": "incident_status_independent_of_task_operation_observation_maintenance",
    }


def _make_executor():
    async def _exec(contract, req):
        try:
            return await execute_project_incident(contract, req)
        except ProjectIncidentError as e:
            raise RuntimeError(f"{e.code}:{e.message}") from e
        except PathViolation as e:
            raise RuntimeError(f"PATH_VIOLATION:{e}") from e

    return _exec


def ensure_project_incident_registered() -> None:
    """Register governance-oriented project.incident capability (no arbitrary execution)."""
    from governance.capability_substrate import get_capability_substrate
    from governance.capability_catalog import (
        CapabilityCatalogEntry,
        catalog_register_entry_for_tests,
    )

    sub = get_capability_substrate()
    sub.register_executor(CAP_PROJECT_INCIDENT, _make_executor())
    schema = {
        "type": "object",
        "required": ["profile"],
        "properties": {
            "profile": {"type": "string", "description": "Allowlisted: incident"},
            "action": {"type": "string"},
            "incident_id": {"type": "string"},
            "maintenance_request_id": {"type": "string"},
            "resolution_observation_id": {"type": "string"},
            "verification_observation_status": {"type": "string"},
            "maintenance_request_status": {"type": "string"},
            "reason": {"type": "string"},
        },
        "additionalProperties": False,
    }
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id=CAP_PROJECT_INCIDENT,
            display_name="Project Incident",
            description=(
                "Governed incident lifecycle. Detect/assess/acknowledge/link/verify/resolve. "
                "Does not auto-remediate, page, or execute shell. Mitigation via Maintain only. "
                "Incident status ≠ observation health ≠ maintenance status ≠ task completion."
            ),
            version="1",
            input_schema=schema,
            output_schema={
                "type": "object",
                "required": ["success", "incident_status"],
                "properties": {
                    "success": {"type": "boolean"},
                    "incident_status": {"type": "string"},
                    "incident": {"type": "object"},
                },
            },
            risk_class="read",
            consequential=False,
            requires_authorization=True,
            required_isolation="none",
            evidence_requirements={"incident_id": True},
            status="active",
            category="incident",
            source="project_incident",
            maintenance={
                "can_be_maintenance_action": False,
                "can_verify_maintenance": False,
                "verification_scope": "none",
            },
        )
    )
