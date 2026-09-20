"""
Governed Workspace & Artifact Capabilities.

Planner proposes relative paths only.
Runtime binds workspace from trusted task context (owner + project).
FileService enforces path containment / symlink resolution.
No shell, no host paths, no planner-selected workspace roots.

Capabilities:
  workspace.list
  artifact.stat
  artifact.read
  artifact.write
  artifact.delete
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from execution.files import FileService, PathViolation, MAX_READ_BYTES, BINARY_EXTENSIONS
from execution.artifacts import content_hash, is_secret_path, ArtifactError

logger = logging.getLogger("devos.workspace_capabilities")

# Hard ceilings (config may lower, never raise past these)
MAX_LIST_ENTRIES = 500
MAX_READ_BYTES_CAP = min(MAX_READ_BYTES, 512_000)  # 512KB planner-facing
MAX_WRITE_BYTES_CAP = 512_000
MAX_PATH_LENGTH = 512
MAX_DEPTH = 12

CAP_WORKSPACE_LIST = "workspace.list"
CAP_ARTIFACT_STAT = "artifact.stat"
CAP_ARTIFACT_READ = "artifact.read"
CAP_ARTIFACT_WRITE = "artifact.write"
CAP_ARTIFACT_DELETE = "artifact.delete"

ALL_CAPS = (
    CAP_WORKSPACE_LIST,
    CAP_ARTIFACT_STAT,
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_ARTIFACT_DELETE,
)

_NULL_RE = re.compile(r"[\x00]")


class WorkspaceCapabilityError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _safe_project_id(raw: Any) -> str:
    s = str(raw or "default").strip() or "default"
    if "\x00" in s or "/" in s or "\\" in s or ".." in s or s in (".", ".."):
        raise WorkspaceCapabilityError("INVALID_PROJECT", f"invalid project_id: {s!r}")
    if len(s) > 64:
        raise WorkspaceCapabilityError("INVALID_PROJECT", "project_id too long")
    return s


def _validate_rel_path(path: Any) -> str:
    if path is None:
        raise WorkspaceCapabilityError("PATH_REQUIRED", "path required")
    if not isinstance(path, str):
        raise WorkspaceCapabilityError("PATH_TYPE", "path must be string")
    if _NULL_RE.search(path):
        raise WorkspaceCapabilityError("PATH_NULL", "null byte in path")
    if len(path) > MAX_PATH_LENGTH:
        raise WorkspaceCapabilityError("PATH_TOO_LONG", "path exceeds maximum length")
    # Encoded traversal / absolute
    raw = path.replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise WorkspaceCapabilityError("PATH_ABSOLUTE", "absolute paths refused")
    # Percent-encoded .. and /
    low = raw.lower()
    if "%2e%2e" in low or "%2f" in low or "%5c" in low:
        raise WorkspaceCapabilityError("PATH_ENCODED", "encoded traversal refused")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise WorkspaceCapabilityError("PATH_TRAVERSAL", "path traversal refused")
    if len(parts) > MAX_DEPTH:
        raise WorkspaceCapabilityError("PATH_DEPTH", "path depth exceeds maximum")
    return "/".join(parts)


def _fs_for_context(context) -> FileService:
    owner = str(getattr(context, "owner_id", "") or "").strip()
    if not owner:
        raise WorkspaceCapabilityError("OWNER_REQUIRED", "owner_id required")
    meta = dict(getattr(context, "metadata", None) or {})
    # Project only from trusted context metadata — never planner workspace_root
    if "workspace_root" in meta:
        raise WorkspaceCapabilityError("WORKSPACE_ROOT_FORBIDDEN", "workspace_root not accepted")
    project_id = _safe_project_id(meta.get("project_id") or "default")
    return FileService(owner, project_id)


def _bounded_list(fs: FileService, rel: str, max_entries: int) -> list[dict]:
    try:
        entries = fs.list_dir(rel) if rel else fs.list_dir("")
    except PathViolation as e:
        raise WorkspaceCapabilityError("PATH_VIOLATION", str(e)) from e
    except FileNotFoundError:
        raise WorkspaceCapabilityError("NOT_FOUND", f"path not found: {rel!r}")
    out = []
    for e in entries[:max_entries]:
        item = {
            "path": e.get("path") or e.get("name") or e.get("rel_path"),
            "type": e.get("type") or ("dir" if e.get("is_dir") else "file"),
            "size": e.get("size"),
        }
        if e.get("mtime") is not None:
            item["modified"] = e.get("mtime")
        out.append(item)
    return out


def _stat_one(fs: FileService, rel: str) -> dict:
    try:
        p = fs._resolve(rel)
    except PathViolation as e:
        raise WorkspaceCapabilityError("PATH_VIOLATION", str(e)) from e
    if not p.exists():
        raise WorkspaceCapabilityError("NOT_FOUND", f"not found: {rel!r}")
    st = p.stat()
    is_dir = p.is_dir()
    digest = None
    if p.is_file() and st.st_size <= MAX_READ_BYTES_CAP:
        try:
            digest = content_hash(p.read_bytes())
        except Exception:
            digest = None
    return {
        "path": rel,
        "type": "dir" if is_dir else "file",
        "size": st.st_size,
        "digest": digest,
        "binary": (not is_dir and p.suffix.lower() in BINARY_EXTENSIONS),
        "secret": is_secret_path(rel),
    }


def _read_one(fs: FileService, rel: str) -> dict:
    if is_secret_path(rel):
        raise WorkspaceCapabilityError("SECRET_PATH", f"secret path content denied: {rel!r}")
    try:
        p = fs._resolve(rel)
    except PathViolation as e:
        raise WorkspaceCapabilityError("PATH_VIOLATION", str(e)) from e
    if not p.exists() or not p.is_file():
        raise WorkspaceCapabilityError("NOT_FOUND", f"file not found: {rel!r}")
    # Symlink: resolve already followed; containment checked by FileService
    if p.is_symlink():
        # Extra fail-closed: refuse reading through symlink
        raise WorkspaceCapabilityError("SYMLINK_REFUSED", "symlink read refused")
    size = p.stat().st_size
    if size > MAX_READ_BYTES_CAP:
        raise WorkspaceCapabilityError("TOO_LARGE", f"file exceeds read limit ({size} > {MAX_READ_BYTES_CAP})")
    data = p.read_bytes()
    digest = content_hash(data)
    if p.suffix.lower() in BINARY_EXTENSIONS:
        return {
            "path": rel,
            "size": size,
            "digest": digest,
            "binary": True,
            "content": None,
            "encoding": None,
            "evidence": {
                "kind": "artifact.read",
                "path": rel,
                "size": size,
                "digest": digest,
                "binary": True,
            },
        }
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "path": rel,
            "size": size,
            "digest": digest,
            "binary": True,
            "content": None,
            "encoding": None,
            "evidence": {
                "kind": "artifact.read",
                "path": rel,
                "size": size,
                "digest": digest,
                "binary": True,
            },
        }
    return {
        "path": rel,
        "size": size,
        "digest": digest,
        "binary": False,
        "content": text,
        "encoding": "utf-8",
        "evidence": {
            "kind": "artifact.read",
            "path": rel,
            "size": size,
            "digest": digest,
            "binary": False,
        },
    }


def _write_one(
    fs: FileService,
    rel: str,
    content: Any,
    *,
    expected_digest: Optional[str] = None,
) -> dict:
    if is_secret_path(rel):
        raise WorkspaceCapabilityError("SECRET_PATH", f"secret path write denied: {rel!r}")
    if content is None:
        raise WorkspaceCapabilityError("CONTENT_REQUIRED", "content required")
    if not isinstance(content, str):
        raise WorkspaceCapabilityError("CONTENT_TYPE", "content must be string")
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES_CAP:
        raise WorkspaceCapabilityError("TOO_LARGE", f"write exceeds limit ({len(data)} > {MAX_WRITE_BYTES_CAP})")
    try:
        p = fs._resolve(rel)
    except PathViolation as e:
        raise WorkspaceCapabilityError("PATH_VIOLATION", str(e)) from e
    before = None
    if p.exists():
        if p.is_dir():
            raise WorkspaceCapabilityError("IS_DIRECTORY", "cannot write directory path")
        if p.is_symlink():
            raise WorkspaceCapabilityError("SYMLINK_REFUSED", "symlink write refused")
        before = content_hash(p.read_bytes())
        if expected_digest is not None and before != expected_digest:
            raise WorkspaceCapabilityError(
                "CONFLICT",
                f"expected_digest mismatch: have {before}, expected {expected_digest}",
            )
    elif expected_digest is not None:
        raise WorkspaceCapabilityError("CONFLICT", "expected_digest set but file does not exist")
    # Ensure parent dirs within root
    p.parent.mkdir(parents=True, exist_ok=True)
    # Re-check parent still in root after mkdir
    try:
        p.parent.resolve().relative_to(fs.root)
    except ValueError as e:
        raise WorkspaceCapabilityError("PATH_VIOLATION", "parent escapes workspace") from e
    p.write_bytes(data)
    after = content_hash(data)
    return {
        "path": rel,
        "size": len(data),
        "digest": after,
        "before_digest": before,
        "created": before is None,
        "evidence": {
            "kind": "artifact.write",
            "path": rel,
            "size": len(data),
            "before_digest": before,
            "after_digest": after,
        },
    }


def _delete_one(fs: FileService, rel: str) -> dict:
    if not rel:
        raise WorkspaceCapabilityError("PATH_REQUIRED", "cannot delete workspace root")
    if is_secret_path(rel):
        raise WorkspaceCapabilityError("SECRET_PATH", f"secret path delete denied: {rel!r}")
    try:
        p = fs._resolve(rel)
    except PathViolation as e:
        raise WorkspaceCapabilityError("PATH_VIOLATION", str(e)) from e
    if p == fs.root:
        raise WorkspaceCapabilityError("ROOT_DELETE", "cannot delete workspace root")
    if not p.exists():
        raise WorkspaceCapabilityError("NOT_FOUND", f"not found: {rel!r}")
    if p.is_dir():
        raise WorkspaceCapabilityError("IS_DIRECTORY", "recursive/directory delete not supported")
    if p.is_symlink():
        raise WorkspaceCapabilityError("SYMLINK_REFUSED", "symlink delete refused")
    before = content_hash(p.read_bytes()) if p.is_file() else None
    size = p.stat().st_size if p.is_file() else 0
    p.unlink()
    return {
        "path": rel,
        "deleted": True,
        "before_digest": before,
        "size": size,
        "evidence": {
            "kind": "artifact.delete",
            "path": rel,
            "before_digest": before,
            "size": size,
        },
    }


async def execute_workspace_capability(contract, req) -> dict:
    """Substrate executor entrypoint."""
    cid = str(getattr(contract, "identity", None) or req.capability_id)
    inputs = dict(req.inputs or {})
    # Reject authority fields from planner
    for banned in (
        "workspace_root", "owner_id", "tenant_id", "isolation",
        "operation_id", "evidence", "granted_capabilities",
    ):
        if banned in inputs:
            raise WorkspaceCapabilityError(
                "FORBIDDEN_FIELD",
                f"planner cannot supply {banned}",
            )
    # Digest supplied by planner is never trusted as authority
    if "digest" in inputs and cid == CAP_ARTIFACT_WRITE:
        # ignore planner digest for write; only expected_digest is a precondition
        inputs.pop("digest", None)

    fs = _fs_for_context(req.context)

    if cid == CAP_WORKSPACE_LIST:
        rel = _validate_rel_path(inputs.get("path") or "") if inputs.get("path") else ""
        if rel:
            _validate_rel_path(rel)
        entries = _bounded_list(fs, rel, MAX_LIST_ENTRIES)
        return {
            "path": rel or ".",
            "entries": entries,
            "truncated": len(entries) >= MAX_LIST_ENTRIES,
            "evidence": {"kind": "workspace.list", "path": rel or ".", "count": len(entries)},
        }

    path = _validate_rel_path(inputs.get("path"))

    if cid == CAP_ARTIFACT_STAT:
        return _stat_one(fs, path)
    if cid == CAP_ARTIFACT_READ:
        return _read_one(fs, path)
    if cid == CAP_ARTIFACT_WRITE:
        return _write_one(
            fs,
            path,
            inputs.get("content"),
            expected_digest=inputs.get("expected_digest"),
        )
    if cid == CAP_ARTIFACT_DELETE:
        return _delete_one(fs, path)

    raise WorkspaceCapabilityError("UNKNOWN_CAPABILITY", f"unknown workspace capability: {cid}")


def _make_executor():
    async def _exec(contract, req):
        try:
            return await execute_workspace_capability(contract, req)
        except WorkspaceCapabilityError as e:
            # Surface structured error through substrate
            raise RuntimeError(f"{e.code}:{e.message}") from e
        except PathViolation as e:
            raise RuntimeError(f"PATH_VIOLATION:{e}") from e
        except ArtifactError as e:
            raise RuntimeError(f"{getattr(e, 'code', 'ARTIFACT')}:{e}") from e

    return _exec


def ensure_workspace_capabilities_registered() -> None:
    """Register contracts + executors + catalog entries (idempotent)."""
    from governance.capability_substrate import (
        CapabilityContract,
        get_capability_substrate,
    )
    from governance.capability_catalog import (
        CapabilityCatalogEntry,
        catalog_register_entry_for_tests,
        get_capability_catalog,
    )

    sub = get_capability_substrate()
    executor = _make_executor()

    specs = [
        (CAP_WORKSPACE_LIST, "List Workspace", "read", False, {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        }),
        (CAP_ARTIFACT_STAT, "Artifact Stat", "read", False, {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        }),
        (CAP_ARTIFACT_READ, "Artifact Read", "read", False, {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        }),
        (CAP_ARTIFACT_WRITE, "Artifact Write", "write", True, {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "expected_digest": {"type": "string"},
            },
        }),
        (CAP_ARTIFACT_DELETE, "Artifact Delete", "destructive", True, {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        }),
    ]

    for identity, name, risk, consequential, schema in specs:
        # Lightweight synthetic contract on substrate via executor registration
        sub.register_executor(identity, executor)
        # Catalog metadata
        catalog_register_entry_for_tests(
            CapabilityCatalogEntry(
                capability_id=identity,
                display_name=name,
                description=f"Governed {name}",
                version="1",
                input_schema=schema,
                risk_class=risk,
                consequential=consequential,
                requires_authorization=True,
                required_isolation="default",
                evidence_requirements={"path": True, "digest": consequential or risk == "read"},
                status="active",
                category="workspace",
                source="workspace_capabilities",
            )
        )

    # Also register descriptors in UCIP registry when available (for authorize_capability_slug)
    try:
        from governance.capability_registry import (
            CapabilityCategory,
            CapabilityDescriptor,
            CapabilityRisk,
            get_registry,
        )

        risk_map = {
            "read": CapabilityRisk.LOW,
            "write": CapabilityRisk.MEDIUM,
            "destructive": CapabilityRisk.HIGH,
        }
        reg = get_registry()
        for identity, name, risk, consequential, schema in specs:
            if reg.get(identity) is None:
                reg.register(
                    CapabilityDescriptor(
                        slug=identity,
                        name=name,
                        category=CapabilityCategory.SYSTEM,
                        description=f"Governed {name}",
                        risk=risk_map.get(risk, CapabilityRisk.MEDIUM),
                        trust_required="read_only" if risk == "read" else "standard",
                        input_schema=schema,
                        output_schema={"type": "object"},
                        metadata={
                            "consequential": consequential,
                            "required_isolation": "default",
                            "status": "active",
                        },
                    )
                )
    except Exception as e:
        logger.debug("registry registration skipped: %s", type(e).__name__)
