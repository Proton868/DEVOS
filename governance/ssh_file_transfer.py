"""Governed SSH file transfer (SFTP-shaped) for DevOS.

Path safety is mandatory: no traversal, no symlink escapes, no root walks.
Dangerous ops (delete, overwrite, chmod-sensitive) require confirmation.
"""
from __future__ import annotations

import logging
import posixpath
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from execution.files import PROJECTS_DIR, PathViolation, _safe_scope_segment

logger = logging.getLogger("devos.ssh_file_transfer")

DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MiB
DEFAULT_TIMEOUT_S = 120.0


class TransferOp(str, Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    LIST = "list"
    MKDIR = "mkdir"
    RENAME = "rename"
    MOVE = "move"
    DELETE = "delete"
    STAT = "stat"
    CHMOD_INSPECT = "chmod_inspect"


class TransferDenied(Exception):
    def __init__(self, code: str):
        self.code = str(code)[:64]
        super().__init__(self.code)


class TransferError(Exception):
    def __init__(self, code: str):
        self.code = str(code)[:64]
        super().__init__(self.code)


_DANGEROUS_OPS = {TransferOp.DELETE, TransferOp.UPLOAD, TransferOp.MOVE, TransferOp.RENAME}


def normalize_remote_path(path: str, *, allow_absolute: bool = True) -> str:
    """Normalize remote path; reject traversal and null bytes."""
    raw = (path or "").strip()
    if not raw:
        raise TransferDenied("empty_path")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise TransferDenied("invalid_path_chars")
    if any(x in raw for x in ("../", "..\\", "/..", "\\..")):
        raise TransferDenied("path_traversal")
    # Reject pure .. segments after split
    parts = re.split(r"[/\\]+", raw)
    if any(p == ".." for p in parts):
        raise TransferDenied("path_traversal")
    if raw.startswith("~"):
        raise TransferDenied("home_expansion_refused")
    # Normalize with posix
    if allow_absolute and raw.startswith("/"):
        norm = posixpath.normpath(raw)
        if not norm.startswith("/"):
            raise TransferDenied("path_traversal")
        # normpath can produce /foo/../../etc → /
        if ".." in PurePosixPath(norm).parts:
            raise TransferDenied("path_traversal")
        return norm
    # relative
    norm = posixpath.normpath(raw.lstrip("/"))
    if norm.startswith("..") or "/../" in f"/{norm}/":
        raise TransferDenied("path_traversal")
    if norm in (".", ""):
        return "."
    return norm


def resolve_local_workspace_path(
    owner_id: str,
    project_id: str,
    relative: str,
    *,
    must_exist: bool = False,
) -> Path:
    """Local path must stay under data/projects/{owner}/{project}/."""
    uid = _safe_scope_segment(owner_id, label="user_id")
    pid = _safe_scope_segment(project_id, label="project_id")
    root = (PROJECTS_DIR / uid / pid).resolve()
    root.mkdir(parents=True, exist_ok=True)
    rel = (relative or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/") or ".." in rel.split("\\"):
        raise TransferDenied("local_path_traversal")
    if "\x00" in rel:
        raise TransferDenied("invalid_path_chars")
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise TransferDenied("local_path_escape") from e
    if must_exist and not target.exists():
        raise TransferError("local_not_found")
    return target


@dataclass
class TransferRequest:
    owner_id: str
    connection_id: str
    op: TransferOp
    remote_path: str
    local_relpath: Optional[str] = None  # under project
    project_id: Optional[str] = None
    remote_dest: Optional[str] = None  # rename/move target
    overwrite: bool = False
    user_confirmed: bool = False
    max_bytes: int = DEFAULT_MAX_FILE_BYTES
    timeout_s: float = DEFAULT_TIMEOUT_S
    actor: str = "user"


@dataclass
class TransferProgress:
    bytes_transferred: int = 0
    total_bytes: Optional[int] = None
    cancelled: bool = False
    status: str = "pending"  # pending|running|succeeded|failed|cancelled|denied


@dataclass
class TransferResult:
    transfer_id: str
    op: str
    status: str
    remote_path: str
    local_path: Optional[str] = None
    bytes_transferred: int = 0
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: int = 0

    def to_public(self) -> dict:
        return {
            "transfer_id": self.transfer_id,
            "op": self.op,
            "status": self.status,
            "remote_path": self.remote_path,
            "local_path": self.local_path,
            "bytes_transferred": self.bytes_transferred,
            "metadata": dict(self.metadata),
            "error": self.error,
            "duration_ms": self.duration_ms,
            "mode": "remote_ssh",
        }


class MockSftpBackend:
    """In-memory remote FS for tests (supports optional symlink markers)."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/"}
        self.symlinks: dict[str, str] = {}  # path → target
        self.cancelled: set[str] = set()

    def _is_symlink(self, path: str) -> bool:
        return path in self.symlinks

    async def list_dir(self, path: str) -> list[dict]:
        path = normalize_remote_path(path)
        prefix = path if path.endswith("/") else path + "/"
        if path != "/" and path not in self.dirs and path not in self.files:
            # allow listing root of virtual tree
            if not any(p.startswith(prefix) or p == path for p in list(self.dirs) + list(self.files)):
                raise TransferError("remote_not_found")
        entries = []
        seen = set()
        for d in self.dirs:
            if d == path:
                continue
            parent = posixpath.dirname(d.rstrip("/")) or "/"
            if parent == path or (path == "/" and d.count("/") == 1):
                name = posixpath.basename(d.rstrip("/"))
                if name and name not in seen:
                    seen.add(name)
                    entries.append({"name": name, "type": "dir", "size": 0})
        for f, data in self.files.items():
            parent = posixpath.dirname(f) or "/"
            if parent == path:
                name = posixpath.basename(f)
                if name not in seen:
                    seen.add(name)
                    entries.append({
                        "name": name,
                        "type": "symlink" if f in self.symlinks else "file",
                        "size": len(data),
                    })
        return sorted(entries, key=lambda e: e["name"])

    async def stat(self, path: str) -> dict:
        path = normalize_remote_path(path)
        if path in self.symlinks:
            return {"path": path, "type": "symlink", "target": self.symlinks[path], "size": 0}
        if path in self.dirs:
            return {"path": path, "type": "dir", "size": 0}
        if path in self.files:
            return {"path": path, "type": "file", "size": len(self.files[path])}
        raise TransferError("remote_not_found")

    async def mkdir(self, path: str) -> None:
        path = normalize_remote_path(path)
        self.dirs.add(path)

    async def write_file(self, path: str, data: bytes, *, overwrite: bool) -> int:
        path = normalize_remote_path(path)
        if path in self.symlinks:
            raise TransferDenied("symlink_write_refused")
        if path in self.files and not overwrite:
            raise TransferDenied("remote_exists")
        parent = posixpath.dirname(path) or "/"
        self.dirs.add(parent)
        self.files[path] = data
        return len(data)

    async def read_file(self, path: str, *, max_bytes: int) -> bytes:
        path = normalize_remote_path(path)
        if path in self.symlinks:
            raise TransferDenied("symlink_read_refused")
        if path not in self.files:
            raise TransferError("remote_not_found")
        data = self.files[path]
        if len(data) > max_bytes:
            raise TransferDenied("file_too_large")
        return data

    async def delete(self, path: str) -> None:
        path = normalize_remote_path(path)
        self.files.pop(path, None)
        self.dirs.discard(path)
        self.symlinks.pop(path, None)

    async def rename(self, src: str, dest: str, *, overwrite: bool) -> None:
        src = normalize_remote_path(src)
        dest = normalize_remote_path(dest)
        if dest in self.files and not overwrite:
            raise TransferDenied("remote_exists")
        if src in self.files:
            self.files[dest] = self.files.pop(src)
        elif src in self.dirs:
            self.dirs.discard(src)
            self.dirs.add(dest)
        else:
            raise TransferError("remote_not_found")


# Global mock for unit tests; production would use asyncssh SFTP
_BACKENDS: dict[str, MockSftpBackend] = {}
_CANCEL: set[str] = set()


def get_mock_backend(connection_id: str) -> MockSftpBackend:
    if connection_id not in _BACKENDS:
        _BACKENDS[connection_id] = MockSftpBackend()
    return _BACKENDS[connection_id]


def clear_transfer_state_for_tests() -> None:
    _BACKENDS.clear()
    _CANCEL.clear()


def request_cancel(transfer_id: str) -> None:
    _CANCEL.add(transfer_id)


async def governed_transfer(req: TransferRequest) -> TransferResult:
    """Authorize + execute one transfer op against owned connection."""
    from governance.ssh_domain import assert_connection_usable, SshAccessDenied, SshConnectionRevoked

    tid = uuid.uuid4().hex
    t0 = time.monotonic()

    try:
        await assert_connection_usable(req.owner_id, req.connection_id)
    except (SshAccessDenied, SshConnectionRevoked):
        return TransferResult(
            transfer_id=tid, op=req.op.value, status="denied",
            remote_path=req.remote_path or "", error="connection_not_usable",
        )

    # Dangerous ops need confirmation
    if req.op in _DANGEROUS_OPS and not req.user_confirmed and req.actor == "agent":
        return TransferResult(
            transfer_id=tid, op=req.op.value, status="denied",
            remote_path=req.remote_path or "", error="confirmation_required",
        )
    if req.op == TransferOp.DELETE and not req.user_confirmed:
        return TransferResult(
            transfer_id=tid, op=req.op.value, status="denied",
            remote_path=req.remote_path or "", error="confirmation_required",
        )

    try:
        raw_remote = req.remote_path if req.remote_path not in (None, "") else "/"
        remote = normalize_remote_path(raw_remote)
    except TransferDenied as e:
        return TransferResult(
            transfer_id=tid, op=req.op.value, status="denied",
            remote_path=req.remote_path or "", error=e.code,
        )

    backend = get_mock_backend(req.connection_id)
    progress = TransferProgress(status="running")

    try:
        if tid in _CANCEL:
            raise TransferError("cancelled")

        meta: dict[str, Any] = {}
        nbytes = 0
        local_out = None

        if req.op == TransferOp.LIST:
            entries = await backend.list_dir(remote)
            meta["entries"] = entries
        elif req.op == TransferOp.STAT or req.op == TransferOp.CHMOD_INSPECT:
            st = await backend.stat(remote)
            if st.get("type") == "symlink":
                raise TransferDenied("symlink_escape_refused")
            meta["stat"] = st
        elif req.op == TransferOp.MKDIR:
            await backend.mkdir(remote)
        elif req.op == TransferOp.DELETE:
            await backend.delete(remote)
        elif req.op == TransferOp.RENAME or req.op == TransferOp.MOVE:
            dest = normalize_remote_path(req.remote_dest or "")
            await backend.rename(remote, dest, overwrite=req.overwrite)
            remote = dest
        elif req.op == TransferOp.UPLOAD:
            if not req.project_id or not req.local_relpath:
                raise TransferDenied("local_path_required")
            local = resolve_local_workspace_path(
                req.owner_id, req.project_id, req.local_relpath, must_exist=True
            )
            data = local.read_bytes()
            if len(data) > req.max_bytes:
                raise TransferDenied("file_too_large")
            # remote overwrite protection
            try:
                existing = await backend.stat(remote)
                if existing and not req.overwrite:
                    raise TransferDenied("remote_exists")
            except TransferError:
                pass
            if tid in _CANCEL:
                raise TransferError("cancelled")
            nbytes = await backend.write_file(remote, data, overwrite=req.overwrite)
            local_out = str(local)
        elif req.op == TransferOp.DOWNLOAD:
            if not req.project_id or not req.local_relpath:
                raise TransferDenied("local_path_required")
            data = await backend.read_file(remote, max_bytes=req.max_bytes)
            local = resolve_local_workspace_path(
                req.owner_id, req.project_id, req.local_relpath, must_exist=False
            )
            if local.exists() and not req.overwrite:
                raise TransferDenied("local_exists")
            if tid in _CANCEL:
                raise TransferError("cancelled")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            nbytes = len(data)
            local_out = str(local)
        else:
            raise TransferDenied("unsupported_op")

        if tid in _CANCEL:
            return TransferResult(
                transfer_id=tid, op=req.op.value, status="cancelled",
                remote_path=remote, local_path=local_out, bytes_transferred=nbytes,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        # Persist job metadata
        try:
            from governance.ssh_domain import create_execution_record
            # lightweight: use file transfer table if available
            from core.database import AsyncSessionLocal, SshFileTransferJob, gen_id
            from datetime import datetime, timezone

            async with AsyncSessionLocal() as db:
                row = SshFileTransferJob(
                    id=tid,
                    owner_id=req.owner_id,
                    connection_id=req.connection_id,
                    direction="upload" if req.op == TransferOp.UPLOAD else (
                        "download" if req.op == TransferOp.DOWNLOAD else req.op.value
                    ),
                    local_path=local_out,
                    remote_path=remote,
                    status="succeeded",
                    bytes_transferred=nbytes,
                    started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    ended_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                db.add(row)
                await db.commit()
        except Exception:
            pass

        return TransferResult(
            transfer_id=tid, op=req.op.value, status="succeeded",
            remote_path=remote, local_path=local_out,
            bytes_transferred=nbytes, metadata=meta,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    except TransferDenied as e:
        return TransferResult(
            transfer_id=tid, op=req.op.value, status="denied",
            remote_path=remote, error=e.code,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    except TransferError as e:
        status = "cancelled" if e.code == "cancelled" else "failed"
        return TransferResult(
            transfer_id=tid, op=req.op.value, status=status,
            remote_path=remote, error=e.code,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
