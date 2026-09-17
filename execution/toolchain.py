"""Universal toolchain registry and status contracts.

PROJECT DEPENDENCY INSTALL (untrusted) vs HOST TOOLCHAIN PROVISION (privileged)
are different trust boundaries. See docs/FLUTTER_TOOLCHAIN.md and HARDENING.md.

Extension point: register_toolchain_profile() / ToolchainProfile in
brain.project_bootstrap. Future ecosystems (Rust, Go, Java, …) register the
same way without changing AgentRuntime.
"""
from __future__ import annotations

from typing import Any, Optional

# Stable error / status codes (do not collapse into generic success/failure)
TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"
DEPENDENCY_INSTALL_FAILED = "dependency_install_failed"
BUILD_FAILED = "build_failed"
TEST_FAILED = "test_failed"
RUNTIME_FAILED = "runtime_failed"
ANALYZE_FAILED = "analyze_failed"
PROVISIONING_PENDING = "provisioning_pending"
PROVISIONING_DENIED = "provisioning_denied"
PROVISIONING_FAILED = "provisioning_failed"
VERSION_INCOMPATIBLE = "version_incompatible"
PLATFORM_UNSUPPORTED = "platform_unsupported"

STATUS_VALUES = frozenset({
    "detected",
    "available",
    "required_version",
    "installed_version",
    "compatible",
    "unavailable",
    "provisioning_pending",
    "provisioning_denied",
    "provisioning_failed",
    "ready",
    "version_incompatible",
    "platform_limited",
})


def map_bootstrap_error(code: Optional[str]) -> Optional[str]:
    """Normalize bootstrap/runner codes to the universal contract."""
    if not code:
        return None
    c = str(code).strip().lower()
    if c in ("install_failed", "dependency_install_failed"):
        return DEPENDENCY_INSTALL_FAILED
    if c in ("build_failed",):
        return BUILD_FAILED
    if c in ("test_failed",):
        return TEST_FAILED
    if c in ("toolchain_unavailable",):
        return TOOLCHAIN_UNAVAILABLE
    if c in ("analyze_failed", "typecheck_failed"):
        return ANALYZE_FAILED
    return c


def list_registered_profiles() -> list[dict]:
    from brain.project_bootstrap import PROFILES
    out = []
    seen = set()
    for k, p in PROFILES.items():
        key = p.kind_value() if hasattr(p, "kind_value") else str(k)
        if key in seen:
            continue
        seen.add(key)
        out.append(p.to_dict() if hasattr(p, "to_dict") else {"kind": key})
    return sorted(out, key=lambda d: d.get("kind") or "")


def structured_status(
    *,
    kind: str,
    detected: bool,
    available: bool,
    required_version: Optional[str] = None,
    installed_version: Optional[str] = None,
    compatible: Optional[bool] = None,
    status: str = "unavailable",
    error: Optional[str] = None,
    platform_limits: Optional[list] = None,
    extra: Optional[dict] = None,
) -> dict[str, Any]:
    if status not in STATUS_VALUES and status not in (
        TOOLCHAIN_UNAVAILABLE, PROVISIONING_PENDING, PROVISIONING_DENIED,
        PROVISIONING_FAILED, VERSION_INCOMPATIBLE, "ready", "detected",
    ):
        status = "unavailable" if not available else status
    body = {
        "kind": kind,
        "detected": bool(detected),
        "available": bool(available),
        "required_version": required_version,
        "installed_version": installed_version,
        "compatible": compatible,
        "status": status,
        "error": error,
        "platform_limits": list(platform_limits or []),
        "trust_boundary": {
            "project_commands": "untrusted_isolation",
            "sdk_provision": "privileged_hitl",
        },
    }
    if extra:
        body.update(extra)
    return body
