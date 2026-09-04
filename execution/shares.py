"""Scoped artifact shares — opaque IDs, not workspace paths."""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from execution.files import FileService, PathViolation
from execution.artifacts import is_secret_path
from execution.durable_store import save_share, get_share_db

# In-process store (durable DB optional later)
_SHARES: dict[str, "ShareRecord"] = {}


@dataclass
class ShareRecord:
    id: str
    user_id: str
    project_id: str
    path: str
    permission: str  # private | shared | public
    created_at: float
    expires_at: Optional[float]
    revoked: bool = False
    revision_hash: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = "revoked" if self.revoked else (
            "expired" if self.expires_at and time.time() > self.expires_at else self.permission
        )
        return d


def create_share(
    user_id: str,
    project_id: str,
    path: str,
    *,
    permission: str = "shared",
    ttl_seconds: Optional[int] = 3600,
    revision_hash: Optional[str] = None,
) -> ShareRecord:
    path = (path or "index.html").lstrip("/")
    if is_secret_path(path):
        raise ValueError("SECRET_DETECTED")
    # ensure exists
    fs = FileService(user_id, project_id)
    try:
        p = fs._resolve(path)
    except PathViolation as e:
        raise ValueError(f"PATH_VIOLATION:{e}") from e
    if not p.is_file():
        raise ValueError("ARTIFACT_NOT_FOUND")
    sid = secrets.token_urlsafe(24)
    exp = time.time() + ttl_seconds if ttl_seconds else None
    rec = ShareRecord(
        id=sid,
        user_id=user_id,
        project_id=project_id,
        path=path,
        permission=permission if permission in ("shared", "public", "private") else "shared",
        created_at=time.time(),
        expires_at=exp,
        revision_hash=revision_hash,
    )
    _SHARES[sid] = rec
    save_share({
        "share_id": sid, "user_id": user_id, "project_id": project_id, "path": path,
        "permission": rec.permission, "status": rec.permission, "revision_hash": revision_hash,
        "created_at": rec.created_at, "expires_at": rec.expires_at, "revoked_at": None,
    })
    return rec


def get_share(share_id: str) -> Optional[ShareRecord]:
    rec = _SHARES.get(share_id)
    if rec is None:
        row = get_share_db(share_id)
        if not row:
            return None
        if row.get("status") == "revoked" or row.get("revoked_at"):
            return None
        if row.get("expires_at") and time.time() > float(row["expires_at"]):
            return None
        rec = ShareRecord(
            id=row["share_id"], user_id=row["user_id"], project_id=row["project_id"],
            path=row["path"], permission=row.get("permission") or "shared",
            created_at=float(row.get("created_at") or time.time()),
            expires_at=float(row["expires_at"]) if row.get("expires_at") else None,
            revoked=False, revision_hash=row.get("revision_hash"),
        )
        _SHARES[share_id] = rec
    if not rec or rec.revoked:
        return None
    if rec.expires_at and time.time() > rec.expires_at:
        return None
    return rec


def revoke_share(share_id: str, user_id: str) -> bool:
    rec = _SHARES.get(share_id)
    if not rec or rec.user_id != user_id:
        return False
    rec.revoked = True
    save_share({
        "share_id": share_id, "user_id": rec.user_id, "project_id": rec.project_id,
        "path": rec.path, "permission": rec.permission, "status": "revoked",
        "created_at": rec.created_at, "expires_at": rec.expires_at, "revoked_at": time.time(),
    })
    return True


def read_share_bytes(share_id: str) -> tuple[ShareRecord, bytes]:
    rec = get_share(share_id)
    if not rec:
        raise LookupError("SHARE_UNAVAILABLE")
    if is_secret_path(rec.path):
        raise PermissionError("SECRET_DETECTED")
    fs = FileService(rec.user_id, rec.project_id)
    data = fs._resolve(rec.path).read_bytes()
    return rec, data
