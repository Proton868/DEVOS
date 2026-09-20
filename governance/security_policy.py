"""
Shared security policy for governed project capabilities.

Consolidates invariants used by validate/build/preview/verify/deploy
and workspace capabilities. Capabilities remain specialized; this module
owns the repeated fail-closed checks so they cannot drift.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


class SecurityPolicyError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# Planner must never supply these as execution controls
PLANNER_FORBIDDEN_EXECUTION_FIELDS = frozenset({
    "command", "shell", "executable", "argv", "cwd", "workspace_root",
    "environment", "env", "docker_args", "host_path", "isolation",
    "script",
})

PLANNER_FORBIDDEN_IDENTITY_FIELDS = frozenset({
    "owner_id", "tenant_id", "project_id", "user_id",
    "runtime_id", "session_id", "target_id", "deployment_id",
    "process_id", "pid",
})

PLANNER_FORBIDDEN_NETWORK_FIELDS = frozenset({
    "port", "ports", "url", "host", "ip", "network", "bind", "domain",
})

PLANNER_FORBIDDEN_RESULT_FIELDS = frozenset({
    "success", "verified", "ready", "healthy", "health", "evidence",
    "digest", "artifact_digest", "expected_success",
})

PLANNER_FORBIDDEN_INFRA_FIELDS = frozenset({
    "provider", "cloud", "account", "region", "cluster", "node",
    "instance_type", "kubernetes", "k8s", "terraform", "docker",
    "firewall", "security_group", "iam", "dns", "tls", "ssl",
    "load_balancer", "autoscaling", "cdn", "billing",
})

ALL_PLANNER_FORBIDDEN = (
    PLANNER_FORBIDDEN_EXECUTION_FIELDS
    | PLANNER_FORBIDDEN_IDENTITY_FIELDS
    | PLANNER_FORBIDDEN_NETWORK_FIELDS
    | PLANNER_FORBIDDEN_RESULT_FIELDS
    | PLANNER_FORBIDDEN_INFRA_FIELDS
)


def reject_planner_forbidden_fields(
    inputs: dict,
    *,
    extra_forbidden: Optional[Iterable[str]] = None,
) -> None:
    """Fail closed if planner-supplied inputs include security-sensitive fields."""
    banned = set(ALL_PLANNER_FORBIDDEN)
    if extra_forbidden:
        banned |= {str(x) for x in extra_forbidden}
    for key in inputs or {}:
        if key in banned:
            raise SecurityPolicyError(
                "FORBIDDEN_FIELD",
                f"planner cannot supply {key}",
            )


def require_owner_id(raw: Any) -> str:
    owner = str(raw or "").strip()
    if not owner:
        raise SecurityPolicyError("OWNER_REQUIRED", "owner_id required")
    if "\x00" in owner or len(owner) > 128:
        raise SecurityPolicyError("INVALID_OWNER", "invalid owner_id")
    return owner


def require_project_id(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        raise SecurityPolicyError("PROJECT_REQUIRED", "project_id required")
    if "\x00" in s or "/" in s or "\\" in s or ".." in s or s in (".", ".."):
        raise SecurityPolicyError("INVALID_PROJECT", f"invalid project_id: {s!r}")
    if len(s) > 64:
        raise SecurityPolicyError("INVALID_PROJECT", "project_id too long")
    return s


def require_relative_path(path: str, *, label: str = "path") -> str:
    raw = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        raise SecurityPolicyError("PATH_REQUIRED", f"{label} required")
    if "\x00" in raw:
        raise SecurityPolicyError("PATH_NULL", f"null byte in {label}")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise SecurityPolicyError("PATH_ABSOLUTE", f"absolute {label} refused")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise SecurityPolicyError("PATH_TRAVERSAL", f"traversal in {label} refused")
    if len(parts) > 16:
        raise SecurityPolicyError("PATH_DEPTH", f"{label} too deep")
    return "/".join(parts)


def assert_ownership_match(
    *,
    expected_owner: str,
    actual_owner: Any,
    expected_project: str,
    actual_project: Any,
    resource: str = "resource",
) -> None:
    if str(actual_owner or "") != expected_owner:
        raise SecurityPolicyError("OWNERSHIP", f"{resource} owner mismatch")
    if str(actual_project or "") != expected_project:
        raise SecurityPolicyError("OWNERSHIP", f"{resource} project mismatch")
