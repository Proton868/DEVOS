"""
Governed project.observe capability.

Read-only observation of a governed deployment target.

Three independent domains:
  Task status      — agentic completion gate
  Operation status — capability execution (SUCCEEDED/FAILED/UNKNOWN)
  Observation status — deployment/runtime observation domain

«Observation describes the observed system. Task status describes the agent
 task. Operation status describes execution of the capability.»

Session-mode honesty: durable session/deployment records are not fabricated
into live HEALTHY process health.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any, Optional

from execution.files import FileService, PathViolation
from execution.project_deploy import DEPLOY_RECORD, STATE_READY as DEPLOY_READY
from execution.project_preview import SESSION_REL, STATE_READY as SESSION_READY
from governance.security_policy import (
    SecurityPolicyError,
    reject_planner_forbidden_fields,
    require_owner_id,
    require_project_id,
    assert_ownership_match,
)

logger = logging.getLogger("devos.project_observe")

CAP_PROJECT_OBSERVE = "project.observe"
PROFILE_OBSERVE = "observe"
ALLOWED_PROFILES = frozenset({PROFILE_OBSERVE})
OBSERVE_CONTRACT = "devos.observe.json"
OBSERVE_RECORD = ".devos/observation.json"

ALLOWED_CHECK_KINDS = frozenset({
    "runtime_state",
    "readiness",
    "health",
    "artifact_identity",
    "deployment_identity",
})

CHECK_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_CHECKS = 16

# Observation status domain (≠ task status ≠ operation status)
OBS_NOT_OBSERVED = "NOT_OBSERVED"
OBS_OBSERVING = "OBSERVING"
OBS_OBSERVED = "OBSERVED"
OBS_UNHEALTHY = "UNHEALTHY"
OBS_UNREADY = "UNREADY"
OBS_DEGRADED = "DEGRADED"
OBS_UNAVAILABLE = "UNAVAILABLE"
OBS_FAILED = "FAILED"

# Check result
CHECK_PASS = "PASS"
CHECK_FAIL = "FAIL"
CHECK_UNAVAILABLE = "UNAVAILABLE"


class ProjectObserveError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _policy(e: SecurityPolicyError) -> ProjectObserveError:
    return ProjectObserveError(e.code, e.message)


def _digest(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def _read_json(fs: FileService, rel: str) -> Optional[dict]:
    try:
        p = fs._resolve(rel)
    except PathViolation:
        return None
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json(fs: FileService, rel: str, data: dict) -> None:
    try:
        p = fs._resolve(rel)
    except PathViolation as e:
        raise ProjectObserveError("PATH_VIOLATION", str(e)) from e
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.parent.resolve().relative_to(fs.root)
    except ValueError as e:
        raise ProjectObserveError("PATH_VIOLATION", "path escapes workspace") from e
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def load_and_validate_observe_contract(fs: FileService) -> dict:
    """Load and strictly validate devos.observe.json."""
    try:
        p = fs._resolve(OBSERVE_CONTRACT)
    except PathViolation as e:
        raise ProjectObserveError("CONTRACT_REQUIRED", str(e)) from e
    if not p.is_file():
        raise ProjectObserveError("CONTRACT_REQUIRED", f"{OBSERVE_CONTRACT} missing")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ProjectObserveError("INVALID_CONTRACT", f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ProjectObserveError("INVALID_CONTRACT", "contract must be object")

    allowed_top = {"version", "profile", "checks"}
    unknown = set(data.keys()) - allowed_top
    if unknown:
        raise ProjectObserveError(
            "INVALID_CONTRACT",
            f"unknown fields: {sorted(unknown)}",
        )

    if data.get("version") != 1:
        raise ProjectObserveError("INVALID_CONTRACT", "version must be 1")
    if str(data.get("profile") or "").strip().lower() != PROFILE_OBSERVE:
        raise ProjectObserveError("INVALID_CONTRACT", "profile must be observe")

    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ProjectObserveError("INVALID_CONTRACT", "checks must be non-empty array")
    if len(checks) > MAX_CHECKS:
        raise ProjectObserveError("INVALID_CONTRACT", f"checks > {MAX_CHECKS}")

    seen_ids: set[str] = set()
    normalized = []
    for c in checks:
        if not isinstance(c, dict):
            raise ProjectObserveError("INVALID_CONTRACT", "check must be object")
        allowed_c = {"id", "kind", "required"}
        unk = set(c.keys()) - allowed_c
        if unk:
            raise ProjectObserveError(
                "INVALID_CONTRACT",
                f"unknown check fields: {sorted(unk)}",
            )
        cid = str(c.get("id") or "")
        if not CHECK_ID_RE.match(cid):
            raise ProjectObserveError("INVALID_CONTRACT", f"invalid check id: {cid!r}")
        if cid in seen_ids:
            raise ProjectObserveError("INVALID_CONTRACT", f"duplicate check id: {cid}")
        seen_ids.add(cid)
        kind = str(c.get("kind") or "")
        if kind not in ALLOWED_CHECK_KINDS:
            raise ProjectObserveError(
                "INVALID_CONTRACT",
                f"unsupported check kind: {kind!r}",
            )
        if "required" not in c or not isinstance(c["required"], bool):
            raise ProjectObserveError("INVALID_CONTRACT", "required must be boolean")
        normalized.append({"id": cid, "kind": kind, "required": bool(c["required"])})

    return {"version": 1, "profile": PROFILE_OBSERVE, "checks": normalized}


def _require_deployment(fs: FileService, *, owner: str, project_id: str) -> dict:
    dep = _read_json(fs, DEPLOY_RECORD)
    if not dep:
        raise ProjectObserveError("DEPLOY_REQUIRED", "deployment record missing")
    try:
        assert_ownership_match(
            expected_owner=owner,
            actual_owner=dep.get("owner_id"),
            expected_project=project_id,
            actual_project=dep.get("project_id"),
            resource="deployment",
        )
    except SecurityPolicyError as e:
        raise _policy(e) from e
    if dep.get("target_type") != "managed_session":
        raise ProjectObserveError(
            "UNSUPPORTED_TARGET",
            f"unsupported target_type: {dep.get('target_type')!r}",
        )
    if dep.get("environment") != "preview":
        raise ProjectObserveError(
            "UNSUPPORTED_ENVIRONMENT",
            f"unsupported environment: {dep.get('environment')!r}",
        )
    return dep


def _eval_check(kind: str, *, dep: dict, session: Optional[dict]) -> dict:
    """Evaluate one check from trusted state. Never from planner claims."""
    mode = (session or {}).get("mode") or dep.get("target_type") or "managed_session"
    is_session = mode in ("session", "managed_session") or session is not None and session.get("mode") == "session"

    if kind == "deployment_identity":
        ok = bool(dep.get("deployment_id") and dep.get("target_id"))
        return {
            "status": CHECK_PASS if ok else CHECK_FAIL,
            "value": dep.get("deployment_id"),
            "source": "deployment_record",
        }

    if kind == "artifact_identity":
        digest = dep.get("artifact_digest")
        ok = bool(digest)
        return {
            "status": CHECK_PASS if ok else CHECK_FAIL,
            "value": digest,
            "source": "deployment_record",
        }

    if kind == "runtime_state":
        # Trusted runtime/session state only
        if session and session.get("runtime_id"):
            state = session.get("state") or "UNKNOWN"
            # Session mode: report state honestly, not fabricated RUNNING process
            return {
                "status": CHECK_PASS if state else CHECK_UNAVAILABLE,
                "value": state,
                "source": "preview_session",
                "mode": session.get("mode") or "session",
            }
        if dep.get("runtime_id"):
            return {
                "status": CHECK_PASS,
                "value": dep.get("state") or "UNKNOWN",
                "source": "deployment_record",
            }
        return {
            "status": CHECK_UNAVAILABLE,
            "value": None,
            "source": "none",
        }

    if kind == "readiness":
        # Session-mode honesty: durable READY session ≠ live process readiness
        if is_session and (not session or session.get("mode") == "session"):
            # Deploy READY means deployment target is READY in managed_session sense
            if dep.get("state") == DEPLOY_READY and dep.get("health", {}).get("ready"):
                return {
                    "status": CHECK_PASS,
                    "value": "READY",
                    "source": "deployment_health",
                    "note": "managed_session_ready",
                }
            return {
                "status": CHECK_UNAVAILABLE,
                "value": "UNAVAILABLE",
                "source": "session_mode",
                "note": "no_live_process_readiness",
            }
        if dep.get("state") == DEPLOY_READY:
            return {"status": CHECK_PASS, "value": "READY", "source": "deployment_record"}
        return {"status": CHECK_FAIL, "value": dep.get("state"), "source": "deployment_record"}

    if kind == "health":
        # Session-mode: do not fabricate HEALTHY for live process
        if is_session and (not session or session.get("mode") == "session"):
            # Managed session deployment can report deployment-level health only
            h = dep.get("health") or {}
            if h.get("ready") and h.get("observed_by") == "project_deploy":
                return {
                    "status": CHECK_PASS,
                    "value": "HEALTHY",
                    "source": "deployment_health",
                    "note": "managed_session_not_live_process",
                }
            return {
                "status": CHECK_UNAVAILABLE,
                "value": "UNAVAILABLE",
                "source": "session_mode",
                "note": "no_live_health_endpoint",
            }
        h = dep.get("health") or {}
        if h.get("ready"):
            return {"status": CHECK_PASS, "value": "HEALTHY", "source": "deployment_health"}
        return {"status": CHECK_FAIL, "value": "UNHEALTHY", "source": "deployment_health"}

    return {"status": CHECK_UNAVAILABLE, "value": None, "source": "unsupported"}


def _aggregate_observation_status(check_results: list[dict], contract_checks: list[dict]) -> str:
    """Derive observation status from check results. Independent of task/operation."""
    by_id = {c["id"]: c for c in check_results}
    required_fail = False
    required_unavail = False
    any_health_fail = False
    any_ready_fail = False
    optional_issues = False

    for spec in contract_checks:
        r = by_id.get(spec["id"], {})
        st = r.get("status")
        if spec["required"]:
            if st == CHECK_FAIL:
                required_fail = True
                if spec["kind"] == "health":
                    any_health_fail = True
                if spec["kind"] == "readiness":
                    any_ready_fail = True
            elif st == CHECK_UNAVAILABLE:
                required_unavail = True
        else:
            if st in (CHECK_FAIL, CHECK_UNAVAILABLE):
                optional_issues = True

    if required_fail:
        if any_health_fail and not any_ready_fail:
            return OBS_UNHEALTHY
        if any_ready_fail:
            return OBS_UNREADY
        return OBS_FAILED
    if required_unavail:
        return OBS_UNAVAILABLE
    if optional_issues:
        return OBS_DEGRADED
    return OBS_OBSERVED


async def execute_project_observe(contract, req) -> dict:
    inputs = dict(req.inputs or {})
    try:
        reject_planner_forbidden_fields(
            inputs,
            extra_forbidden={
                "healthy", "observation", "observation_id", "status",
                "checks", "headers", "credentials",
            },
        )
    except SecurityPolicyError as e:
        raise _policy(e) from e

    profile = str(inputs.get("profile") or "").strip().lower()
    if profile != PROFILE_OBSERVE:
        raise ProjectObserveError("UNSUPPORTED_PROFILE", f"unsupported profile: {profile!r}")
    for k in inputs:
        if k != "profile":
            raise ProjectObserveError("FORBIDDEN_FIELD", f"unknown field: {k}")

    ctx = req.context
    try:
        owner = require_owner_id(getattr(ctx, "owner_id", None))
        meta = dict(getattr(ctx, "metadata", None) or {})
        project_id = require_project_id(meta.get("project_id"))
    except SecurityPolicyError as e:
        raise _policy(e) from e

    fs = FileService(owner, project_id)
    ocontract = load_and_validate_observe_contract(fs)
    dep = _require_deployment(fs, owner=owner, project_id=project_id)
    session = _read_json(fs, SESSION_REL)

    check_results = []
    for spec in ocontract["checks"]:
        evaluated = _eval_check(spec["kind"], dep=dep, session=session)
        entry = {
            "id": spec["id"],
            "kind": spec["kind"],
            "required": spec["required"],
            "status": evaluated["status"],
            "value": evaluated.get("value"),
            "source": evaluated.get("source"),
            "evidence_digest": _digest({
                "id": spec["id"],
                "kind": spec["kind"],
                "status": evaluated["status"],
                "value": evaluated.get("value"),
                "deployment_id": dep.get("deployment_id"),
            }),
        }
        if evaluated.get("note"):
            entry["note"] = evaluated["note"]
        check_results.append(entry)

    obs_status = _aggregate_observation_status(check_results, ocontract["checks"])
    observation_id = "obs_" + uuid.uuid4().hex[:16]

    # Idempotent identity: same deployment + same check outcomes → stable logical obs
    existing = _read_json(fs, OBSERVE_RECORD)
    if (
        existing
        and existing.get("deployment_id") == dep.get("deployment_id")
        and existing.get("artifact_digest") == dep.get("artifact_digest")
        and existing.get("status") == obs_status
        and existing.get("checks")
    ):
        # Converge on existing observation_id when result is equivalent
        prev_checks = existing.get("checks") or []
        if len(prev_checks) == len(check_results) and all(
            prev_checks[i].get("status") == check_results[i].get("status")
            and prev_checks[i].get("id") == check_results[i].get("id")
            for i in range(len(check_results))
        ):
            observation_id = existing.get("observation_id") or observation_id

    record = {
        "observation_id": observation_id,
        "deployment_id": dep.get("deployment_id"),
        "target_id": dep.get("target_id"),
        "runtime_id": dep.get("runtime_id") or (session or {}).get("runtime_id"),
        "project_id": project_id,
        "owner_id": owner,
        "artifact_digest": dep.get("artifact_digest"),
        "verification_digest": dep.get("verification_digest"),
        "status": obs_status,
        "observed_at": time.time(),
        "checks": check_results,
        "domains": {
            "observation_status": obs_status,
            "operation_status": "SUCCEEDED",  # read completed; observation may be UNHEALTHY
            "note": "observation_status_independent_of_task_status",
        },
    }
    _write_json(fs, OBSERVE_RECORD, record)

    evidence_body = {
        "kind": CAP_PROJECT_OBSERVE,
        "profile": PROFILE_OBSERVE,
        "project_id": project_id,
        "owner_id": owner,
        "observation_id": observation_id,
        "deployment_id": dep.get("deployment_id"),
        "status": obs_status,
        "checks": [{"id": c["id"], "kind": c["kind"], "status": c["status"]} for c in check_results],
        "artifact_digest": dep.get("artifact_digest"),
        "read_only": True,
    }
    evid_hash = _digest(evidence_body)
    evidence_body["digest"] = evid_hash

    # Operation success means observation executed; not that app is healthy
    return {
        "success": True,  # operation SUCCEEDED
        "status": "succeeded",  # operation status
        "observation_status": obs_status,
        "profile": PROFILE_OBSERVE,
        "project_id": project_id,
        "observation": record,
        "diagnostics": {
            "summary": f"observation={obs_status}",
            "read_only": True,
            "operation_independent_of_health": True,
        },
        "isolation": {
            "required": False,
            "actual": "none_required",
            "policy": "read_only_observe",
        },
        "exit_code": 0,
        "evidence": evidence_body,
        "evidence_digest": evid_hash,
    }


def _make_executor():
    async def _exec(contract, req):
        try:
            return await execute_project_observe(contract, req)
        except ProjectObserveError as e:
            raise RuntimeError(f"{e.code}:{e.message}") from e
        except PathViolation as e:
            raise RuntimeError(f"PATH_VIOLATION:{e}") from e

    return _exec


def ensure_project_observe_registered() -> None:
    from governance.capability_substrate import get_capability_substrate
    from governance.capability_catalog import (
        CapabilityCatalogEntry,
        catalog_register_entry_for_tests,
    )

    sub = get_capability_substrate()
    sub.register_executor(CAP_PROJECT_OBSERVE, _make_executor())
    schema = {
        "type": "object",
        "required": ["profile"],
        "properties": {
            "profile": {"type": "string", "description": "Allowlisted: observe"},
        },
        "additionalProperties": False,
    }
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id=CAP_PROJECT_OBSERVE,
            display_name="Project Observe",
            description=(
                "Governed read-only deployment observation. "
                "Observation status ≠ task completion. Not a monitoring platform."
            ),
            version="1",
            input_schema=schema,
            output_schema={
                "type": "object",
                "required": ["success", "observation_status", "observation"],
                "properties": {
                    "success": {"type": "boolean"},
                    "observation_status": {"type": "string"},
                    "observation": {"type": "object"},
                    "evidence": {"type": "object"},
                },
            },
            risk_class="read",
            consequential=False,
            requires_authorization=True,
            required_isolation="none",
            evidence_requirements={"observation_id": True, "checks": True},
            status="active",
            category="observe",
            source="project_observe",
        )
    )
    try:
        from governance.capability_registry import (
            CapabilityCategory,
            CapabilityDescriptor,
            CapabilityRisk,
            get_registry,
        )

        reg = get_registry()
        if reg.get(CAP_PROJECT_OBSERVE) is None:
            reg.register(
                CapabilityDescriptor(
                    slug=CAP_PROJECT_OBSERVE,
                    name="Project Observe",
                    category=CapabilityCategory.SYSTEM,
                    description="Governed project observation (read-only)",
                    risk=CapabilityRisk.LOW,
                    trust_required="read_only",
                    input_schema=schema,
                    output_schema={"type": "object"},
                    metadata={
                        "consequential": False,
                        "read_only": True,
                        "required_isolation": "none",
                        "status": "active",
                        "profiles": sorted(ALLOWED_PROFILES),
                    },
                )
            )
    except Exception as e:
        logger.debug("registry register skipped: %s", type(e).__name__)
