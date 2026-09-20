"""
Governed project.validate capability.

Planner may only select an allowlisted profile (e.g. structure|test|verify).
No arbitrary commands, argv, shell, workspace_root, or isolation overrides.

Path:
  catalog → runtime validation → UCIP → (consequential) Operation/Job path
  → existing runtime/isolation OR pure structure inspection → trusted result → evidence
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

from execution.files import FileService, PathViolation

logger = logging.getLogger("devos.project_validate")

CAP_PROJECT_VALIDATE = "project.validate"

# Allowlisted profiles only — planner cannot redefine meaning
PROFILE_STRUCTURE = "structure"  # static workspace inspection (no process)
PROFILE_TEST = "test"            # existing runtime lifecycle test action
PROFILE_VERIFY = "verify"        # structure + basic project markers

ALLOWED_PROFILES = frozenset({PROFILE_STRUCTURE, PROFILE_TEST, PROFILE_VERIFY})

MAX_DIAG_CHARS = 2000
MAX_FILE_MARKERS = 40


class ProjectValidateError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _safe_project_id(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        raise ProjectValidateError("PROJECT_REQUIRED", "project_id required")
    if "\x00" in s or "/" in s or "\\" in s or ".." in s or s in (".", ".."):
        raise ProjectValidateError("INVALID_PROJECT", f"invalid project_id: {s!r}")
    if len(s) > 64:
        raise ProjectValidateError("INVALID_PROJECT", "project_id too long")
    return s


def _profile(raw: Any) -> str:
    p = str(raw or "").strip().lower()
    if not p:
        raise ProjectValidateError("PROFILE_REQUIRED", "profile required")
    if p not in ALLOWED_PROFILES:
        raise ProjectValidateError("UNSUPPORTED_PROFILE", f"unsupported profile: {p!r}")
    return p


def _bound(s: str, n: int = MAX_DIAG_CHARS) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 12] + "\n…[truncated]"


def _digest_payload(payload: dict) -> str:
    raw = repr(sorted(payload.items())).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def _structure_validate(fs: FileService) -> dict:
    """Pure filesystem inspection — no process execution."""
    try:
        entries = fs.list_dir("")
    except FileNotFoundError:
        entries = []
    except PathViolation as e:
        raise ProjectValidateError("PATH_VIOLATION", str(e)) from e

    names = [e.get("name") or e.get("path") for e in entries]
    markers = []
    for candidate in (
        "package.json", "pyproject.toml", "requirements.txt", "Cargo.toml",
        "go.mod", "index.html", "README.md", "src", "tests", "test",
    ):
        if candidate in names:
            markers.append(candidate)

    # Fail if workspace is empty
    ok = len(entries) > 0
    diagnostics = {
        "entry_count": len(entries),
        "markers": markers[:MAX_FILE_MARKERS],
        "empty": not ok,
    }
    if not ok:
        diagnostics["summary"] = "workspace is empty"
    else:
        diagnostics["summary"] = f"structure ok ({len(entries)} top-level entries)"
    return {
        "success": ok,
        "status": "passed" if ok else "failed",
        "profile": PROFILE_STRUCTURE,
        "diagnostics": diagnostics,
        "isolation": {
            "required": False,
            "actual": "none_required",
            "policy": "structure_static",
        },
        "exit_code": 0 if ok else 1,
    }


def _verify_validate(fs: FileService) -> dict:
    """Structure + require at least one project marker."""
    base = _structure_validate(fs)
    markers = list((base.get("diagnostics") or {}).get("markers") or [])
    ok = bool(base.get("success")) and len(markers) > 0
    diag = dict(base.get("diagnostics") or {})
    if not markers:
        diag["summary"] = "no recognized project markers"
        ok = False
    else:
        diag["summary"] = f"verify ok markers={markers[:8]}"
    return {
        "success": ok,
        "status": "passed" if ok else "failed",
        "profile": PROFILE_VERIFY,
        "diagnostics": diag,
        "isolation": {
            "required": False,
            "actual": "none_required",
            "policy": "verify_static",
        },
        "exit_code": 0 if ok else 1,
    }


async def _test_validate(user_id: str, project_id: str) -> dict:
    """Delegate to existing runtime lifecycle test action (isolated)."""
    from execution.runtime_service import run_lifecycle_action
    from execution.app_runtime import AppRuntimeState

    t0 = time.perf_counter()
    try:
        snap = await run_lifecycle_action(user_id, project_id, "test")
    except ValueError as e:
        raise ProjectValidateError("INVALID_ACTION", str(e)) from e
    except Exception as e:
        raise ProjectValidateError("RUNTIME_ERROR", f"{type(e).__name__}:{e}") from e

    duration_ms = int((time.perf_counter() - t0) * 1000)
    state = str(snap.state or "")
    evidence = dict(snap.evidence or {})
    exit_code = evidence.get("exit_code")
    if exit_code is None:
        exit_code = 0 if state in ("READY", "ready") else 1
    success = state in ("READY", "ready") and int(exit_code) == 0
    # Isolation evidence from runtime when present
    isolation = evidence.get("isolation") or {
        "required": True,
        "actual": evidence.get("isolation_mode") or "runtime_managed",
        "policy": "untrusted",
    }
    return {
        "success": bool(success),
        "status": "passed" if success else "failed",
        "profile": PROFILE_TEST,
        "diagnostics": {
            "summary": _bound(snap.detail or ("tests passed" if success else "tests failed")),
            "state": state,
            "health": snap.health,
            "log_tail": _bound(snap.logs_tail or "", 1500),
            "detection_kind": (snap.detection or {}).get("kind"),
        },
        "isolation": isolation if isinstance(isolation, dict) else {"actual": str(isolation)},
        "exit_code": int(exit_code),
        "duration_ms": duration_ms,
    }


async def execute_project_validate(contract, req) -> dict:
    inputs = dict(req.inputs or {})
    # Reject free-form execution controls from planner
    for banned in (
        "command", "shell", "executable", "argv", "cwd", "workspace_root",
        "environment", "env", "docker_args", "host_path", "isolation",
        "owner_id", "tenant_id", "success", "verified", "tests_passed",
        "exit_code", "evidence",
    ):
        if banned in inputs:
            raise ProjectValidateError("FORBIDDEN_FIELD", f"planner cannot supply {banned}")

    ctx = req.context
    owner = str(getattr(ctx, "owner_id", "") or "").strip()
    if not owner:
        raise ProjectValidateError("OWNER_REQUIRED", "owner_id required")

    meta = dict(getattr(ctx, "metadata", None) or {})
    project_id = _safe_project_id(meta.get("project_id"))
    profile = _profile(inputs.get("profile"))

    fs = FileService(owner, project_id)

    if profile == PROFILE_STRUCTURE:
        result = _structure_validate(fs)
    elif profile == PROFILE_VERIFY:
        result = _verify_validate(fs)
    else:
        result = await _test_validate(owner, project_id)

    # Trusted evidence — never from planner
    evidence_body = {
        "kind": CAP_PROJECT_VALIDATE,
        "profile": profile,
        "project_id": project_id,
        "owner_id": owner,
        "success": bool(result.get("success")),
        "status": result.get("status"),
        "exit_code": result.get("exit_code"),
        "isolation": result.get("isolation"),
    }
    evidence_digest = _digest_payload(evidence_body)
    evidence_body["digest"] = evidence_digest

    out = {
        "success": bool(result.get("success")),
        "status": result.get("status"),
        "profile": profile,
        "project_id": project_id,
        "diagnostics": result.get("diagnostics") or {},
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms"),
        "isolation": result.get("isolation"),
        "evidence": evidence_body,
        "evidence_digest": evidence_digest,
    }
    # Bound diagnostics again
    if isinstance(out["diagnostics"], dict) and "log_tail" in out["diagnostics"]:
        out["diagnostics"]["log_tail"] = _bound(out["diagnostics"]["log_tail"], 1500)
    return out


def _make_executor():
    async def _exec(contract, req):
        try:
            return await execute_project_validate(contract, req)
        except ProjectValidateError as e:
            raise RuntimeError(f"{e.code}:{e.message}") from e
        except PathViolation as e:
            raise RuntimeError(f"PATH_VIOLATION:{e}") from e

    return _exec


def ensure_project_validate_registered() -> None:
    """Register project.validate with substrate, registry, and catalog."""
    from governance.capability_substrate import get_capability_substrate
    from governance.capability_catalog import (
        CapabilityCatalogEntry,
        catalog_register_entry_for_tests,
    )

    sub = get_capability_substrate()
    sub.register_executor(CAP_PROJECT_VALIDATE, _make_executor())

    schema = {
        "type": "object",
        "required": ["profile"],
        "properties": {
            "profile": {
                "type": "string",
                "description": "Allowlisted validation profile: structure|test|verify",
            },
        },
        "additionalProperties": False,
    }
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id=CAP_PROJECT_VALIDATE,
            display_name="Project Validate",
            description="Governed project validation (profile-based; no arbitrary commands)",
            version="1",
            input_schema=schema,
            output_schema={
                "type": "object",
                "required": ["success", "status", "profile", "evidence"],
                "properties": {
                    "success": {"type": "boolean"},
                    "status": {"type": "string"},
                    "profile": {"type": "string"},
                    "evidence": {"type": "object"},
                },
            },
            risk_class="write",  # execution-class validation
            consequential=True,
            requires_authorization=True,
            required_isolation="restricted",
            evidence_requirements={"profile": True, "success": True, "digest": True},
            status="active",
            category="validation",
            source="project_validate",
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
        if reg.get(CAP_PROJECT_VALIDATE) is None:
            reg.register(
                CapabilityDescriptor(
                    slug=CAP_PROJECT_VALIDATE,
                    name="Project Validate",
                    category=CapabilityCategory.SYSTEM,
                    description="Governed project validation (profile-based)",
                    risk=CapabilityRisk.MEDIUM,
                    trust_required="standard",
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
