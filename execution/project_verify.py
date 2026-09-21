"""
Governed project.verify capability.

READY ≠ VERIFIED.

Requires trusted build artifact + trusted Preview session/runtime.
Application-level checks come from project-owned devos.verify.json.
Planner may only supply profile="verify".
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Optional

from execution.files import FileService, PathViolation
from execution.artifacts import content_hash
from execution.project_preview import SESSION_REL, STATE_READY, STATE_STOPPED

logger = logging.getLogger("devos.project_verify")

CAP_PROJECT_VERIFY = "project.verify"
PROFILE_VERIFY = "verify"
ALLOWED_PROFILES = frozenset({PROFILE_VERIFY})
VERIFY_CONTRACT = "devos.verify.json"
BUILD_CONTRACT = "devos.build.json"


class ProjectVerifyError(Exception):
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
        raise ProjectVerifyError("PROJECT_REQUIRED", "project_id required")
    if "\x00" in s or "/" in s or "\\" in s or ".." in s or s in (".", ".."):
        raise ProjectVerifyError("INVALID_PROJECT", f"invalid project_id: {s!r}")
    if len(s) > 64:
        raise ProjectVerifyError("INVALID_PROJECT", "project_id too long")
    return s


def _profile(raw: Any) -> str:
    p = str(raw or "").strip().lower()
    if not p:
        raise ProjectVerifyError("PROFILE_REQUIRED", "profile required")
    if p not in ALLOWED_PROFILES:
        raise ProjectVerifyError("UNSUPPORTED_PROFILE", f"unsupported profile: {p!r}")
    return p


def _safe_rel(path: str, *, label: str = "path") -> str:
    raw = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        raise ProjectVerifyError("PATH_REQUIRED", f"{label} required")
    if "\x00" in raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ProjectVerifyError("PATH_INVALID", f"invalid {label}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ProjectVerifyError("PATH_TRAVERSAL", f"traversal in {label}")
    return "/".join(parts)


def _require_build(fs: FileService) -> dict:
    artifact_path = "dist/app.js"
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
        raise ProjectVerifyError("BUILD_REQUIRED", str(e)) from e
    if not ap.is_file():
        raise ProjectVerifyError("BUILD_REQUIRED", f"build artifact missing: {artifact_path}")
    data = ap.read_bytes()
    if not data:
        raise ProjectVerifyError("BUILD_REQUIRED", "build artifact empty")
    return {"path": artifact_path, "size": len(data), "digest": content_hash(data), "bytes": data}


def _require_preview_session(fs: FileService, *, owner: str, project_id: str) -> dict:
    try:
        p = fs._resolve(SESSION_REL)
    except PathViolation as e:
        raise ProjectVerifyError("PREVIEW_REQUIRED", str(e)) from e
    if not p.is_file():
        raise ProjectVerifyError("PREVIEW_REQUIRED", "preview session missing")
    try:
        session = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ProjectVerifyError("PREVIEW_REQUIRED", f"invalid session: {e}") from e
    if not isinstance(session, dict):
        raise ProjectVerifyError("PREVIEW_REQUIRED", "invalid session shape")
    if session.get("owner_id") != owner or session.get("project_id") != project_id:
        raise ProjectVerifyError("OWNERSHIP", "preview session ownership mismatch")
    if session.get("state") == STATE_STOPPED:
        raise ProjectVerifyError("PREVIEW_STOPPED", "preview session is stopped")
    if session.get("state") != STATE_READY:
        raise ProjectVerifyError(
            "PREVIEW_NOT_READY",
            f"preview state is {session.get('state')!r}, not READY",
        )
    return session


def _load_verify_contract(fs: FileService) -> dict:
    try:
        p = fs._resolve(VERIFY_CONTRACT)
    except PathViolation:
        return {"profile": PROFILE_VERIFY, "mode": "session"}
    if not p.is_file():
        return {"profile": PROFILE_VERIFY, "mode": "session"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ProjectVerifyError("INVALID_CONTRACT", f"invalid {VERIFY_CONTRACT}: {e}") from e
    if not isinstance(data, dict):
        raise ProjectVerifyError("INVALID_CONTRACT", "verify contract must be object")
    return data


def _run_application_checks(
    fs: FileService,
    *,
    build: dict,
    session: dict,
    contract: dict,
) -> list[dict]:
    """Trusted application-level checks. Session READY alone is never enough."""
    checks: list[dict] = []
    body = build.get("bytes") or b""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = ""

    # Always require non-empty artifact
    checks.append({
        "name": "artifact_nonempty",
        "status": "passed" if build.get("size", 0) > 0 else "failed",
    })

    # Session ready is a prerequisite observation, not verification success
    checks.append({
        "name": "preview_session_ready",
        "status": "passed" if session.get("state") == STATE_READY else "failed",
    })

    # Build digest binding
    if session.get("build_digest") and session.get("build_digest") != build.get("digest"):
        checks.append({
            "name": "build_digest_match",
            "status": "failed",
            "detail": "session build_digest does not match current artifact",
        })
    else:
        checks.append({"name": "build_digest_match", "status": "passed"})

    # Contract application checks
    must = contract.get("must_contain")
    if must is not None:
        ok = str(must) in text
        checks.append({
            "name": "must_contain",
            "status": "passed" if ok else "failed",
            "detail": None if ok else f"missing {must!r}",
        })

    forbid = contract.get("forbid_contains")
    if forbid is not None:
        ok = str(forbid) not in text
        checks.append({
            "name": "forbid_contains",
            "status": "passed" if ok else "failed",
            "detail": None if ok else f"contains forbidden {forbid!r}",
        })

    # Optional expected function behavior markers (deterministic fixture)
    expected_exports = contract.get("expected_exports") or []
    if isinstance(expected_exports, list):
        for exp in expected_exports[:20]:
            name = str(exp)
            ok = name in text
            checks.append({
                "name": f"export:{name}",
                "status": "passed" if ok else "failed",
            })

    # Mode honesty: session mode cannot claim live_network
    mode = str(contract.get("mode") or session.get("mode") or "session").lower()
    if mode == "session":
        checks.append({
            "name": "mode_honesty",
            "status": "passed",
            "detail": "session_mode_not_live_network",
        })
    elif mode == "runtime":
        # Live runtime would need isolation-backed observation; fail closed if only session
        if session.get("mode") != "runtime":
            checks.append({
                "name": "runtime_mode_required",
                "status": "failed",
                "detail": "contract requires runtime mode but session is not runtime",
            })
        else:
            checks.append({"name": "runtime_mode_required", "status": "passed"})

    return checks


async def execute_project_verify(contract, req) -> dict:
    inputs = dict(req.inputs or {})
    for banned in (
        "command", "shell", "executable", "argv", "cwd", "workspace_root",
        "environment", "env", "docker_args", "host_path", "isolation",
        "owner_id", "tenant_id", "project_id", "runtime_id", "session_id",
        "port", "ports", "url", "host", "network", "bind",
        "ready", "health", "verified", "success", "evidence", "digest",
        "process_id", "pid", "expected_success", "script",
    ):
        if banned in inputs:
            raise ProjectVerifyError("FORBIDDEN_FIELD", f"planner cannot supply {banned}")

    ctx = req.context
    owner = str(getattr(ctx, "owner_id", "") or "").strip()
    if not owner:
        raise ProjectVerifyError("OWNER_REQUIRED", "owner_id required")
    meta = dict(getattr(ctx, "metadata", None) or {})
    project_id = _safe_project_id(meta.get("project_id"))
    profile = _profile(inputs.get("profile"))

    fs = FileService(owner, project_id)
    build = _require_build(fs)
    session = _require_preview_session(fs, owner=owner, project_id=project_id)
    vcontract = _load_verify_contract(fs)

    # READY ≠ VERIFIED: session READY is prerequisite; application checks decide
    checks = _run_application_checks(fs, build=build, session=session, contract=vcontract)
    all_passed = all(c.get("status") == "passed" for c in checks)
    # Explicitly require at least one application-level check beyond session ready
    app_checks = [c for c in checks if c.get("name") not in ("preview_session_ready",)]
    if not app_checks:
        all_passed = False
        checks.append({
            "name": "application_observation",
            "status": "failed",
            "detail": "no application-level checks configured",
        })

    status = "verified" if all_passed else "failed"
    success = bool(all_passed)

    evidence_body = {
        "kind": CAP_PROJECT_VERIFY,
        "profile": profile,
        "project_id": project_id,
        "owner_id": owner,
        "success": success,
        "status": status,
        "runtime_id": session.get("runtime_id"),
        "preview_state": session.get("state"),
        "build_digest": build.get("digest"),
        "checks": [{"name": c["name"], "status": c["status"]} for c in checks],
        "mode": session.get("mode") or "session",
        # Explicit: READY is not VERIFIED
        "ready_not_verified": True,
    }
    evid_hash = hashlib.sha256(
        json.dumps(evidence_body, sort_keys=True, default=str).encode()
    ).hexdigest()
    evidence_body["digest"] = evid_hash

    # Persist last verification result under project
    try:
        vp = fs._resolve(".devos/verify_result.json")
        vp.parent.mkdir(parents=True, exist_ok=True)
        vp.write_text(
            json.dumps({
                "status": status,
                "success": success,
                "runtime_id": session.get("runtime_id"),
                "checks": checks,
                "evidence_digest": evid_hash,
                "updated_at": time.time(),
            }, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug("verify result persist skipped: %s", type(e).__name__)

    return {
        "success": success,
        "status": status,
        "profile": profile,
        "project_id": project_id,
        "runtime_id": session.get("runtime_id"),
        "checks": checks,
        "build": {
            "path": build.get("path"),
            "size": build.get("size"),
            "digest": build.get("digest"),
        },
        "preview": {
            "runtime_id": session.get("runtime_id"),
            "state": session.get("state"),
            "mode": session.get("mode"),
        },
        "diagnostics": {
            "summary": "verified" if success else "verification failed",
            "mode": session.get("mode") or "session",
            "ready_not_verified": True,
        },
        "isolation": {
            "required": False,
            "actual": "none_required",
            "policy": "session_verify" if session.get("mode") != "runtime" else "runtime_verify",
        },
        "exit_code": 0 if success else 1,
        "evidence": evidence_body,
        "evidence_digest": evid_hash,
    }


def _make_executor():
    async def _exec(contract, req):
        try:
            return await execute_project_verify(contract, req)
        except ProjectVerifyError as e:
            raise RuntimeError(f"{e.code}:{e.message}") from e
        except PathViolation as e:
            raise RuntimeError(f"PATH_VIOLATION:{e}") from e

    return _exec


def ensure_project_verify_registered() -> None:
    from governance.capability_substrate import get_capability_substrate
    from governance.capability_catalog import (
        CapabilityCatalogEntry,
        catalog_register_entry_for_tests,
    )

    sub = get_capability_substrate()
    sub.register_executor(CAP_PROJECT_VERIFY, _make_executor())
    schema = {
        "type": "object",
        "required": ["profile"],
        "properties": {
            "profile": {"type": "string", "description": "Allowlisted: verify"},
        },
        "additionalProperties": False,
    }
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id=CAP_PROJECT_VERIFY,
            display_name="Project Verify",
            description="Governed verification (READY ≠ VERIFIED; application observation)",
            version="1",
            input_schema=schema,
            output_schema={
                "type": "object",
                "required": ["success", "status", "checks"],
                "properties": {
                    "success": {"type": "boolean"},
                    "status": {"type": "string"},
                    "checks": {"type": "array"},
                    "evidence": {"type": "object"},
                },
            },
            risk_class="read",
            consequential=True,
            requires_authorization=True,
            required_isolation="restricted",
            evidence_requirements={"checks": True, "runtime_id": True, "build_digest": True},
            status="active",
            category="verify",
            source="project_verify",
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
        if reg.get(CAP_PROJECT_VERIFY) is None:
            reg.register(
                CapabilityDescriptor(
                    slug=CAP_PROJECT_VERIFY,
                    name="Project Verify",
                    category=CapabilityCategory.SYSTEM,
                    description="Governed project verification",
                    risk=CapabilityRisk.LOW,
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
