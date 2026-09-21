"""
Governed project.preview capability.

Reuses execution/runtime_service.py — no second runtime engine.

Planner may only supply profile="preview".
Build prerequisite must be established from trusted workspace state.
Durable session identity is project/owner scoped.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from execution.files import FileService, PathViolation
from execution.artifacts import content_hash

logger = logging.getLogger("devos.project_preview")

CAP_PROJECT_PREVIEW = "project.preview"
PROFILE_PREVIEW = "preview"
ALLOWED_PROFILES = frozenset({PROFILE_PREVIEW})
PREVIEW_CONTRACT = "devos.preview.json"
BUILD_CONTRACT = "devos.build.json"
SESSION_REL = ".devos/preview_session.json"

# Runtime states (aligned with app_runtime where applicable)
STATE_CREATED = "CREATED"
STATE_STARTING = "STARTING"
STATE_RUNNING = "RUNNING"
STATE_READY = "READY"
STATE_STOPPING = "STOPPING"
STATE_STOPPED = "STOPPED"
STATE_FAILED = "FAILED"
STATE_UNKNOWN = "UNKNOWN"


class ProjectPreviewError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _bound(s: str, n: int = 2000) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 12] + "\n…[truncated]"


def _safe_project_id(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        raise ProjectPreviewError("PROJECT_REQUIRED", "project_id required")
    if "\x00" in s or "/" in s or "\\" in s or ".." in s or s in (".", ".."):
        raise ProjectPreviewError("INVALID_PROJECT", f"invalid project_id: {s!r}")
    if len(s) > 64:
        raise ProjectPreviewError("INVALID_PROJECT", "project_id too long")
    return s


def _profile(raw: Any) -> str:
    p = str(raw or "").strip().lower()
    if not p:
        raise ProjectPreviewError("PROFILE_REQUIRED", "profile required")
    if p not in ALLOWED_PROFILES:
        raise ProjectPreviewError("UNSUPPORTED_PROFILE", f"unsupported profile: {p!r}")
    return p


def _safe_rel(path: str, *, label: str = "path") -> str:
    raw = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        raise ProjectPreviewError("PATH_REQUIRED", f"{label} required")
    if "\x00" in raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ProjectPreviewError("PATH_INVALID", f"invalid {label}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ProjectPreviewError("PATH_TRAVERSAL", f"traversal in {label}")
    return "/".join(parts)


def _require_build_artifact(fs: FileService) -> dict:
    """Trusted build prerequisite from workspace (not planner claims)."""
    artifact_path = "dist/app.js"
    build_digest = None
    try:
        bp = fs._resolve(BUILD_CONTRACT)
        if bp.is_file():
            data = json.loads(bp.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("artifact"):
                artifact_path = _safe_rel(str(data["artifact"]), label="artifact")
    except Exception:
        pass
    try:
        ap = fs._resolve(artifact_path)
    except PathViolation as e:
        raise ProjectPreviewError("BUILD_REQUIRED", f"build artifact path invalid: {e}") from e
    if not ap.is_file():
        raise ProjectPreviewError(
            "BUILD_REQUIRED",
            f"successful build artifact missing: {artifact_path}",
        )
    data = ap.read_bytes()
    if not data:
        raise ProjectPreviewError("BUILD_REQUIRED", "build artifact empty")
    build_digest = content_hash(data)
    return {
        "path": artifact_path,
        "size": len(data),
        "digest": build_digest,
    }


def _load_preview_contract(fs: FileService) -> dict:
    try:
        p = fs._resolve(PREVIEW_CONTRACT)
    except PathViolation:
        return {"mode": "session", "profile": PROFILE_PREVIEW}
    if not p.is_file():
        return {"mode": "session", "profile": PROFILE_PREVIEW}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ProjectPreviewError("INVALID_CONTRACT", f"invalid {PREVIEW_CONTRACT}: {e}") from e
    if not isinstance(data, dict):
        raise ProjectPreviewError("INVALID_CONTRACT", "preview contract must be object")
    return data


def _read_session(fs: FileService) -> Optional[dict]:
    try:
        p = fs._resolve(SESSION_REL)
    except PathViolation:
        return None
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_session(fs: FileService, session: dict) -> None:
    try:
        p = fs._resolve(SESSION_REL)
    except PathViolation as e:
        raise ProjectPreviewError("PATH_VIOLATION", str(e)) from e
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.parent.resolve().relative_to(fs.root)
    except ValueError as e:
        raise ProjectPreviewError("PATH_VIOLATION", "session path escapes workspace") from e
    p.write_text(json.dumps(session, indent=2, sort_keys=True), encoding="utf-8")


def _session_preview(
    fs: FileService,
    *,
    owner: str,
    project_id: str,
    build_info: dict,
    contract: dict,
) -> dict:
    """Durable session preview without host process (deterministic tests / no isolation)."""
    existing = _read_session(fs)
    # Idempotent: same owner/project + same build digest → same logical runtime
    if (
        existing
        and existing.get("owner_id") == owner
        and existing.get("project_id") == project_id
        and existing.get("build_digest") == build_info["digest"]
        and existing.get("state") == STATE_READY
    ):
        return {
            "success": True,
            "status": "succeeded",
            "profile": PROFILE_PREVIEW,
            "runtime": existing,
            "diagnostics": {
                "summary": "preview session reused",
                "mode": "session",
                "idempotent": True,
            },
            "isolation": {
                "required": False,
                "actual": "none_required",
                "policy": "session_preview",
            },
            "exit_code": 0,
        }

    runtime_id = "rt_" + uuid.uuid4().hex[:16]
    now = time.time()
    session = {
        "runtime_id": runtime_id,
        "owner_id": owner,
        "project_id": project_id,
        "state": STATE_READY,
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
        "build_digest": build_info["digest"],
        "build_artifact": build_info["path"],
        "mode": "session",
        "readiness": {
            "ready": True,
            "reason": "build_artifact_present",
            "observed_by": "project_preview",
        },
    }
    _write_session(fs, session)
    return {
        "success": True,
        "status": "succeeded",
        "profile": PROFILE_PREVIEW,
        "runtime": session,
        "diagnostics": {
            "summary": f"preview session {runtime_id} ready",
            "mode": "session",
            "idempotent": False,
        },
        "isolation": {
            "required": False,
            "actual": "none_required",
            "policy": "session_preview",
        },
        "exit_code": 0,
    }


async def _runtime_preview(
    fs: FileService,
    *,
    owner: str,
    project_id: str,
    build_info: dict,
) -> dict:
    """Use existing runtime_service lifecycle start — isolation required for process."""
    from execution.runtime_service import run_lifecycle_action, snapshot
    from execution.isolation import detect_backends

    backends = detect_backends()
    if not backends.get("suitable_for_untrusted_code") and not backends.get("available"):
        raise ProjectPreviewError(
            "ISOLATION_UNAVAILABLE",
            "required isolation backend unavailable for process preview",
        )

    t0 = time.time()
    try:
        snap = await run_lifecycle_action(owner, project_id, "start")
    except Exception as e:
        raise ProjectPreviewError("RUNTIME_ERROR", f"{type(e).__name__}:{e}") from e

    state = str(snap.state or STATE_FAILED)
    success = state in ("READY", "ready", "RUNNING", "running")
    runtime_id = f"rt_{owner}_{project_id}"
    session = {
        "runtime_id": runtime_id,
        "owner_id": owner,
        "project_id": project_id,
        "state": STATE_READY if success else STATE_FAILED,
        "created_at": t0,
        "updated_at": time.time(),
        "build_digest": build_info["digest"],
        "build_artifact": build_info["path"],
        "mode": "runtime",
        "pid": snap.pid,
        "port": snap.port,
        "readiness": {
            "ready": success,
            "reason": snap.detail or state,
            "health": snap.health,
            "observed_by": "runtime_service",
        },
    }
    _write_session(fs, session)
    return {
        "success": success,
        "status": "succeeded" if success else "failed",
        "profile": PROFILE_PREVIEW,
        "runtime": session,
        "diagnostics": {
            "summary": _bound(snap.detail or state),
            "mode": "runtime",
            "state": state,
        },
        "isolation": (snap.evidence or {}).get("isolation")
        or {"required": True, "actual": "runtime_managed", "policy": "untrusted"},
        "exit_code": 0 if success else 1,
    }


async def execute_project_preview(contract, req) -> dict:
    inputs = dict(req.inputs or {})
    for banned in (
        "command", "shell", "executable", "argv", "cwd", "workspace_root",
        "environment", "env", "docker_args", "host_path", "isolation",
        "owner_id", "tenant_id", "project_id", "port", "ports", "url",
        "runtime_id", "pid", "process_id", "ready", "health", "success",
        "evidence", "network", "bind", "host",
    ):
        if banned in inputs:
            raise ProjectPreviewError("FORBIDDEN_FIELD", f"planner cannot supply {banned}")

    ctx = req.context
    owner = str(getattr(ctx, "owner_id", "") or "").strip()
    if not owner:
        raise ProjectPreviewError("OWNER_REQUIRED", "owner_id required")
    meta = dict(getattr(ctx, "metadata", None) or {})
    project_id = _safe_project_id(meta.get("project_id"))
    profile = _profile(inputs.get("profile"))

    fs = FileService(owner, project_id)
    build_info = _require_build_artifact(fs)
    pcontract = _load_preview_contract(fs)
    mode = str(pcontract.get("mode") or "session").strip().lower()

    if mode == "runtime":
        result = await _runtime_preview(fs, owner=owner, project_id=project_id, build_info=build_info)
    else:
        result = _session_preview(
            fs, owner=owner, project_id=project_id, build_info=build_info, contract=pcontract,
        )

    runtime = result.get("runtime") or {}
    evidence_body = {
        "kind": CAP_PROJECT_PREVIEW,
        "profile": profile,
        "project_id": project_id,
        "owner_id": owner,
        "success": bool(result.get("success")),
        "status": result.get("status"),
        "runtime_id": runtime.get("runtime_id"),
        "state": runtime.get("state"),
        "build_digest": build_info["digest"],
        "readiness": runtime.get("readiness"),
        "isolation": result.get("isolation"),
    }
    evid_hash = hashlib.sha256(
        json.dumps(evidence_body, sort_keys=True, default=str).encode()
    ).hexdigest()
    evidence_body["digest"] = evid_hash

    return {
        "success": bool(result.get("success")),
        "status": result.get("status"),
        "profile": profile,
        "project_id": project_id,
        "runtime": runtime,
        "build": build_info,
        "diagnostics": result.get("diagnostics") or {},
        "isolation": result.get("isolation"),
        "exit_code": result.get("exit_code"),
        "evidence": evidence_body,
        "evidence_digest": evid_hash,
    }


def stop_preview_session(owner: str, project_id: str) -> dict:
    """Governed stop: mark session STOPPED; never kill arbitrary host PIDs."""
    fs = FileService(owner, project_id)
    session = _read_session(fs)
    if not session:
        return {"success": True, "status": "stopped", "runtime": None}
    if session.get("owner_id") != owner or session.get("project_id") != project_id:
        raise ProjectPreviewError("OWNERSHIP", "session ownership mismatch")
    session["state"] = STATE_STOPPED
    session["updated_at"] = time.time()
    session["readiness"] = {"ready": False, "reason": "stopped", "observed_by": "project_preview"}
    _write_session(fs, session)
    return {"success": True, "status": "stopped", "runtime": session}


def _make_executor():
    async def _exec(contract, req):
        try:
            return await execute_project_preview(contract, req)
        except ProjectPreviewError as e:
            raise RuntimeError(f"{e.code}:{e.message}") from e
        except PathViolation as e:
            raise RuntimeError(f"PATH_VIOLATION:{e}") from e

    return _exec


def ensure_project_preview_registered() -> None:
    from governance.capability_substrate import get_capability_substrate
    from governance.capability_catalog import (
        CapabilityCatalogEntry,
        catalog_register_entry_for_tests,
    )

    sub = get_capability_substrate()
    sub.register_executor(CAP_PROJECT_PREVIEW, _make_executor())
    schema = {
        "type": "object",
        "required": ["profile"],
        "properties": {
            "profile": {"type": "string", "description": "Allowlisted: preview"},
        },
        "additionalProperties": False,
    }
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id=CAP_PROJECT_PREVIEW,
            display_name="Project Preview",
            description="Governed preview/runtime session (build prerequisite required)",
            version="1",
            input_schema=schema,
            output_schema={
                "type": "object",
                "required": ["success", "status", "runtime"],
                "properties": {
                    "success": {"type": "boolean"},
                    "runtime": {"type": "object"},
                    "evidence": {"type": "object"},
                },
            },
            risk_class="write",
            consequential=True,
            requires_authorization=True,
            required_isolation="restricted",
            evidence_requirements={"runtime_id": True, "readiness": True, "build_digest": True},
            status="active",
            category="preview",
            source="project_preview",
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
        if reg.get(CAP_PROJECT_PREVIEW) is None:
            reg.register(
                CapabilityDescriptor(
                    slug=CAP_PROJECT_PREVIEW,
                    name="Project Preview",
                    category=CapabilityCategory.SYSTEM,
                    description="Governed project preview/runtime",
                    risk=CapabilityRisk.MEDIUM,
                    trust_required="operator",
                    input_schema=schema,
                    output_schema={"type": "object"},
                    metadata={
                        "consequential": True,
                        "required_isolation": "restricted",
                        "status": "active",
                        "profiles": sorted(ALLOWED_PROFILES),
                    },
                )
            )
    except Exception as e:
        logger.debug("registry register skipped: %s", type(e).__name__)
