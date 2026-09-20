"""
Governed project.deploy capability.

SCOPE GATE
----------
This module creates/manages a **bounded deployment target**.

It is NOT responsible for:
  cloud provisioning, DNS, TLS, K8s, Terraform, IAM, autoscaling,
  load balancers, CDN, multi-region, billing, secret platforms.

Supported target type (this milestone): managed_session
Supported environment (this milestone): preview

«Deploy governs a bounded deployment target; it does not become
 DevOS's infrastructure control plane.»
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, Optional

from execution.files import FileService, PathViolation
from execution.artifacts import content_hash
from execution.project_preview import SESSION_REL, STATE_READY
from governance.security_policy import (
    SecurityPolicyError,
    reject_planner_forbidden_fields,
    require_owner_id,
    require_project_id,
    assert_ownership_match,
)

logger = logging.getLogger("devos.project_deploy")

CAP_PROJECT_DEPLOY = "project.deploy"
PROFILE_DEPLOY = "deploy"
ALLOWED_PROFILES = frozenset({PROFILE_DEPLOY})

# Explicit scope gate
SUPPORTED_TARGET_TYPES = frozenset({"managed_session"})
SUPPORTED_ENVIRONMENTS = frozenset({"preview"})

BUILD_CONTRACT = "devos.build.json"
VERIFY_RESULT = ".devos/verify_result.json"
DEPLOY_RECORD = ".devos/deployment.json"

STATE_REQUESTED = "REQUESTED"
STATE_DEPLOYING = "DEPLOYING"
STATE_READY = "READY"
STATE_FAILED = "FAILED"
STATE_STOPPED = "STOPPED"
STATE_UNKNOWN = "UNKNOWN"


class ProjectDeployError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _policy_to_deploy(e: SecurityPolicyError) -> ProjectDeployError:
    return ProjectDeployError(e.code, e.message)


def _require_build(fs: FileService) -> dict:
    artifact_path = "dist/app.js"
    try:
        bp = fs._resolve(BUILD_CONTRACT)
        if bp.is_file():
            data = json.loads(bp.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("artifact"):
                from governance.security_policy import require_relative_path
                artifact_path = require_relative_path(str(data["artifact"]), label="artifact")
    except SecurityPolicyError as e:
        raise _policy_to_deploy(e) from e
    except Exception:
        pass
    try:
        ap = fs._resolve(artifact_path)
    except PathViolation as e:
        raise ProjectDeployError("BUILD_REQUIRED", str(e)) from e
    if not ap.is_file():
        raise ProjectDeployError("BUILD_REQUIRED", f"build artifact missing: {artifact_path}")
    data = ap.read_bytes()
    if not data:
        raise ProjectDeployError("BUILD_REQUIRED", "build artifact empty")
    return {"path": artifact_path, "size": len(data), "digest": content_hash(data)}


def _require_verify(fs: FileService, *, build_digest: str, owner: str, project_id: str) -> dict:
    try:
        p = fs._resolve(VERIFY_RESULT)
    except PathViolation as e:
        raise ProjectDeployError("VERIFY_REQUIRED", str(e)) from e
    if not p.is_file():
        raise ProjectDeployError("VERIFY_REQUIRED", "verification result missing")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ProjectDeployError("VERIFY_REQUIRED", f"invalid verify result: {e}") from e
    if not isinstance(data, dict):
        raise ProjectDeployError("VERIFY_REQUIRED", "invalid verify result shape")
    if data.get("status") != "verified" or not data.get("success"):
        raise ProjectDeployError("VERIFY_REQUIRED", "project not verified")
    # Artifact immutability: current build must match what was verified
    # Prefer evidence digest chain via preview session build_digest
    session = _read_json(fs, SESSION_REL)
    if session:
        try:
            assert_ownership_match(
                expected_owner=owner,
                actual_owner=session.get("owner_id"),
                expected_project=project_id,
                actual_project=session.get("project_id"),
                resource="preview_session",
            )
        except SecurityPolicyError as e:
            raise _policy_to_deploy(e) from e
        if session.get("build_digest") and session.get("build_digest") != build_digest:
            raise ProjectDeployError(
                "ARTIFACT_MISMATCH",
                "build artifact changed after verification; rebuild and re-verify",
            )
        if session.get("state") != STATE_READY:
            raise ProjectDeployError("PREVIEW_REQUIRED", "preview session not READY")
    return data


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
        raise ProjectDeployError("PATH_VIOLATION", str(e)) from e
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.parent.resolve().relative_to(fs.root)
    except ValueError as e:
        raise ProjectDeployError("PATH_VIOLATION", "path escapes workspace") from e
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _validate_target_type(raw: Any) -> str:
    t = str(raw or "managed_session").strip().lower()
    if t not in SUPPORTED_TARGET_TYPES:
        raise ProjectDeployError(
            "UNSUPPORTED_TARGET",
            f"unsupported target_type {t!r}; supported={sorted(SUPPORTED_TARGET_TYPES)}",
        )
    return t


def _validate_environment(raw: Any) -> str:
    e = str(raw or "preview").strip().lower()
    if e not in SUPPORTED_ENVIRONMENTS:
        raise ProjectDeployError(
            "UNSUPPORTED_ENVIRONMENT",
            f"unsupported environment {e!r}; supported={sorted(SUPPORTED_ENVIRONMENTS)}",
        )
    return e


async def execute_project_deploy(contract, req) -> dict:
    inputs = dict(req.inputs or {})
    try:
        reject_planner_forbidden_fields(
            inputs,
            extra_forbidden={"artifact", "artifact_path", "rollback", "manifest"},
        )
    except SecurityPolicyError as e:
        raise _policy_to_deploy(e) from e

    profile = str(inputs.get("profile") or "").strip().lower()
    if profile != PROFILE_DEPLOY:
        raise ProjectDeployError("UNSUPPORTED_PROFILE", f"unsupported profile: {profile!r}")

    # Scope gate: only allowlisted optional semantic fields
    for k in inputs:
        if k not in ("profile", "environment", "target_type"):
            # already rejected forbidden; any other unknown → fail
            if k not in ("profile",):
                if k not in ("environment", "target_type"):
                    raise ProjectDeployError("FORBIDDEN_FIELD", f"unknown field: {k}")

    ctx = req.context
    try:
        owner = require_owner_id(getattr(ctx, "owner_id", None))
        meta = dict(getattr(ctx, "metadata", None) or {})
        project_id = require_project_id(meta.get("project_id"))
    except SecurityPolicyError as e:
        raise _policy_to_deploy(e) from e

    target_type = _validate_target_type(inputs.get("target_type"))
    environment = _validate_environment(inputs.get("environment"))

    fs = FileService(owner, project_id)
    build = _require_build(fs)
    verify = _require_verify(fs, build_digest=build["digest"], owner=owner, project_id=project_id)

    # Idempotent: same owner/project/artifact/target → same deployment
    existing = _read_json(fs, DEPLOY_RECORD)
    if (
        existing
        and existing.get("owner_id") == owner
        and existing.get("project_id") == project_id
        and existing.get("artifact_digest") == build["digest"]
        and existing.get("target_type") == target_type
        and existing.get("environment") == environment
        and existing.get("state") == STATE_READY
    ):
        return _result(existing, build, verify, idempotent=True)

    # Safe replacement boundary: if existing READY with different artifact, do not destroy
    if (
        existing
        and existing.get("state") == STATE_READY
        and existing.get("artifact_digest")
        and existing.get("artifact_digest") != build["digest"]
    ):
        raise ProjectDeployError(
            "ACTIVE_DEPLOYMENT",
            "active deployment exists for different artifact; stop/replace not auto-destructive",
        )

    now = time.time()
    deployment_id = "dep_" + uuid.uuid4().hex[:16]
    target_id = f"tgt_{target_type}_{environment}_{project_id}"
    session = _read_json(fs, SESSION_REL) or {}
    runtime_id = session.get("runtime_id")

    record = {
        "deployment_id": deployment_id,
        "target_id": target_id,
        "target_type": target_type,
        "environment": environment,
        "owner_id": owner,
        "project_id": project_id,
        "artifact_path": build["path"],
        "artifact_digest": build["digest"],
        "verification_status": verify.get("status"),
        "verification_digest": verify.get("evidence_digest"),
        "runtime_id": runtime_id,
        "state": STATE_READY,
        "health": {
            "ready": True,
            "reason": "managed_session_deployment",
            "observed_by": "project_deploy",
        },
        "endpoint": None,  # no fake external endpoint
        "scope_gate": {
            "infrastructure_control_plane": False,
            "supported_target_types": sorted(SUPPORTED_TARGET_TYPES),
            "supported_environments": sorted(SUPPORTED_ENVIRONMENTS),
        },
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
    }
    _write_json(fs, DEPLOY_RECORD, record)
    return _result(record, build, verify, idempotent=False)


def _result(record: dict, build: dict, verify: dict, *, idempotent: bool) -> dict:
    evidence_body = {
        "kind": CAP_PROJECT_DEPLOY,
        "profile": PROFILE_DEPLOY,
        "project_id": record.get("project_id"),
        "owner_id": record.get("owner_id"),
        "success": record.get("state") == STATE_READY,
        "status": "deployed" if record.get("state") == STATE_READY else "failed",
        "deployment_id": record.get("deployment_id"),
        "target_id": record.get("target_id"),
        "artifact_digest": record.get("artifact_digest"),
        "verification_digest": record.get("verification_digest"),
        "runtime_id": record.get("runtime_id"),
        "health": record.get("health"),
        "scope_gate": record.get("scope_gate"),
    }
    evid_hash = hashlib.sha256(
        json.dumps(evidence_body, sort_keys=True, default=str).encode()
    ).hexdigest()
    evidence_body["digest"] = evid_hash
    success = record.get("state") == STATE_READY
    return {
        "success": success,
        "status": "deployed" if success else "failed",
        "profile": PROFILE_DEPLOY,
        "project_id": record.get("project_id"),
        "deployment": record,
        "build": build,
        "diagnostics": {
            "summary": "deployed" if success else "deploy failed",
            "idempotent": idempotent,
            "scope_gate": True,
        },
        "isolation": {
            "required": False,
            "actual": "none_required",
            "policy": "managed_session_deploy",
        },
        "exit_code": 0 if success else 1,
        "evidence": evidence_body,
        "evidence_digest": evid_hash,
    }


def stop_deployment(owner: str, project_id: str) -> dict:
    fs = FileService(owner, project_id)
    rec = _read_json(fs, DEPLOY_RECORD)
    if not rec:
        return {"success": True, "status": "stopped", "deployment": None}
    try:
        assert_ownership_match(
            expected_owner=owner,
            actual_owner=rec.get("owner_id"),
            expected_project=project_id,
            actual_project=rec.get("project_id"),
            resource="deployment",
        )
    except SecurityPolicyError as e:
        raise _policy_to_deploy(e) from e
    rec["state"] = STATE_STOPPED
    rec["health"] = {"ready": False, "reason": "stopped", "observed_by": "project_deploy"}
    rec["updated_at"] = time.time()
    _write_json(fs, DEPLOY_RECORD, rec)
    return {"success": True, "status": "stopped", "deployment": rec}


def _make_executor():
    async def _exec(contract, req):
        try:
            return await execute_project_deploy(contract, req)
        except ProjectDeployError as e:
            raise RuntimeError(f"{e.code}:{e.message}") from e
        except PathViolation as e:
            raise RuntimeError(f"PATH_VIOLATION:{e}") from e

    return _exec


def ensure_project_deploy_registered() -> None:
    from governance.capability_substrate import get_capability_substrate
    from governance.capability_catalog import (
        CapabilityCatalogEntry,
        catalog_register_entry_for_tests,
    )

    sub = get_capability_substrate()
    sub.register_executor(CAP_PROJECT_DEPLOY, _make_executor())
    schema = {
        "type": "object",
        "required": ["profile"],
        "properties": {
            "profile": {"type": "string"},
            "environment": {"type": "string"},
            "target_type": {"type": "string"},
        },
        "additionalProperties": False,
    }
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id=CAP_PROJECT_DEPLOY,
            display_name="Project Deploy",
            description=(
                "Governed bounded deployment target (managed_session/preview only). "
                "Not an infrastructure control plane."
            ),
            version="1",
            input_schema=schema,
            output_schema={
                "type": "object",
                "required": ["success", "status", "deployment"],
                "properties": {
                    "success": {"type": "boolean"},
                    "deployment": {"type": "object"},
                    "evidence": {"type": "object"},
                },
            },
            risk_class="write",
            consequential=True,
            requires_authorization=True,
            required_isolation="restricted",
            evidence_requirements={
                "deployment_id": True,
                "artifact_digest": True,
                "verification_digest": True,
            },
            status="active",
            category="deploy",
            source="project_deploy",
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
        if reg.get(CAP_PROJECT_DEPLOY) is None:
            reg.register(
                CapabilityDescriptor(
                    slug=CAP_PROJECT_DEPLOY,
                    name="Project Deploy",
                    category=CapabilityCategory.SYSTEM,
                    description="Governed project deployment (scoped)",
                    risk=CapabilityRisk.HIGH,
                    trust_required="standard",
                    input_schema=schema,
                    output_schema={"type": "object"},
                    metadata={
                        "consequential": True,
                        "required_isolation": "restricted",
                        "status": "active",
                        "scope_gate": True,
                        "supported_target_types": sorted(SUPPORTED_TARGET_TYPES),
                        "supported_environments": sorted(SUPPORTED_ENVIRONMENTS),
                    },
                )
            )
    except Exception as e:
        logger.debug("registry register skipped: %s", type(e).__name__)
