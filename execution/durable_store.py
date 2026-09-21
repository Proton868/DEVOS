"""Delivery durable store — Postgres SoT (runtimes, shares)."""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional


def init_store() -> None:
    return


def _coerce_fields(*args, **kwargs) -> dict:
    """Accept either a single mapping or keyword fields (canonical internal API)."""
    if len(args) == 1 and isinstance(args[0], dict):
        out = dict(args[0])
        out.update(kwargs)
        return out
    if args:
        raise TypeError(
            "expected a single dict or keyword fields, got %d positional args" % len(args)
        )
    return dict(kwargs)



def new_id(prefix: str = "") -> str:
    """Generate a durable identifier, optionally with a caller-defined prefix."""
    return f"{prefix}{uuid.uuid4()}"


def upsert_runtime(*args, **kwargs) -> str:
    from core.sync_session import get_sync_session
    from core.database import DeliveryRuntime

    fields = _coerce_fields(*args, **kwargs)
    rid = fields.get("runtime_id") or new_id()
    with get_sync_session() as s:
        row = s.get(DeliveryRuntime, rid)
        data = {
            "runtime_id": rid,
            "user_id": fields.get("user_id") or "",
            "project_id": fields.get("project_id") or "",
            "status": fields.get("status"),
            "pid": fields.get("pid"),
            "port": fields.get("port"),
            "command": fields.get("command"),
            "cwd": fields.get("cwd"),
            "app_type": fields.get("app_type"),
            "revision": fields.get("revision"),
            "isolation_mode": fields.get("isolation_mode"),
            "last_error": fields.get("last_error"),
            "created_at": fields.get("created_at") or time.time(),
            "started_at": fields.get("started_at"),
            "stopped_at": fields.get("stopped_at"),
            "meta": fields.get("meta") or {},
        }
        if row is None:
            s.add(DeliveryRuntime(**data))
        else:
            for k, v in data.items():
                if k != "runtime_id":
                    setattr(row, k, v)
        s.commit()
    return rid


def get_runtime(runtime_id: str) -> Optional[dict]:
    from core.sync_session import get_sync_session
    from core.database import DeliveryRuntime

    with get_sync_session() as s:
        r = s.get(DeliveryRuntime, runtime_id)
        if not r:
            return None
        return {
            "runtime_id": r.runtime_id,
            "user_id": r.user_id,
            "project_id": r.project_id,
            "status": r.status,
            "pid": r.pid,
            "port": r.port,
            "meta": r.meta,
        }


def list_runtimes(user_id: str, project_id: Optional[str] = None) -> list[dict]:
    from core.sync_session import get_sync_session
    from core.database import DeliveryRuntime
    from sqlalchemy import select

    with get_sync_session() as s:
        stmt = select(DeliveryRuntime).where(DeliveryRuntime.user_id == user_id)
        if project_id:
            stmt = stmt.where(DeliveryRuntime.project_id == project_id)
        rows = s.execute(stmt).scalars().all()
        return [
            {"runtime_id": r.runtime_id, "user_id": r.user_id, "status": r.status, "project_id": r.project_id}
            for r in rows
        ]


def list_runtimes_for_project(project_id: str) -> list[dict]:
    """List durable runtimes belonging to one project."""
    from core.sync_session import get_sync_session
    from core.database import DeliveryRuntime
    from sqlalchemy import select

    with get_sync_session() as s:
        stmt = select(DeliveryRuntime).where(
            DeliveryRuntime.project_id == project_id
        )
        rows = s.execute(stmt).scalars().all()
        return [
            {
                "runtime_id": r.runtime_id,
                "user_id": r.user_id,
                "status": r.status,
                "project_id": r.project_id,
                "pid": r.pid,
            }
            for r in rows
        ]


def append_log(runtime_id: str, line: str) -> None:
    # logs stay filesystem/ephemeral — not domain authority
    return


def read_logs(runtime_id: str, after_id: int = 0, limit: int = 200) -> list:
    """Return recent log rows for a runtime (empty list if none persisted yet)."""
    # Structured rows preferred by log_stream; plain strings also acceptable.
    return []



def save_share(*args, **kwargs) -> str:
    from core.sync_session import get_sync_session
    from core.database import DeliveryShare

    fields = _coerce_fields(*args, **kwargs)
    sid = fields.get("share_id") or new_id()
    with get_sync_session() as s:
        row = s.get(DeliveryShare, sid)
        data = {
            "share_id": sid,
            "user_id": fields.get("user_id") or "",
            "project_id": fields.get("project_id") or "",
            "path": fields.get("path") or "",
            "permission": fields.get("permission"),
            "status": fields.get("status"),
            "revision_hash": fields.get("revision_hash"),
            "created_at": fields.get("created_at") or time.time(),
            "expires_at": fields.get("expires_at"),
            "revoked_at": fields.get("revoked_at"),
        }
        if row is None:
            s.add(DeliveryShare(**data))
        else:
            for k, v in data.items():
                if k != "share_id":
                    setattr(row, k, v)
        s.commit()
    return sid


def get_share_db(share_id: str) -> Optional[dict]:
    from core.sync_session import get_sync_session
    from core.database import DeliveryShare

    with get_sync_session() as s:
        r = s.get(DeliveryShare, share_id)
        if not r:
            return None
        return {
            "share_id": r.share_id,
            "user_id": r.user_id,
            "project_id": r.project_id,
            "path": r.path,
            "status": r.status,
        }


def save_deployment(*args, **kwargs) -> str:
    fields = _coerce_fields(*args, **kwargs)
    # store as meta on a synthetic runtime id for minimal surface
    return upsert_runtime(
        runtime_id=fields.get("deployment_id") or new_id(),
        user_id=fields.get("user_id") or "",
        project_id=fields.get("project_id") or "",
        status=fields.get("status"),
        meta={"kind": "deployment", **{k: v for k, v in fields.items() if k not in ("user_id", "project_id", "status")}},
    )


def get_deployment(deployment_id: str) -> Optional[dict]:
    r = get_runtime(deployment_id)
    if r and (r.get("meta") or {}).get("kind") == "deployment":
        return r
    return r


def save_tunnel(*args, **kwargs) -> str:
    fields = _coerce_fields(*args, **kwargs)
    return upsert_runtime(
        runtime_id=fields.get("tunnel_id") or new_id(),
        user_id=fields.get("user_id") or "",
        project_id=fields.get("project_id") or "",
        status=fields.get("status"),
        meta={"kind": "tunnel", **fields},
    )


def get_tunnel(tunnel_id: str) -> Optional[dict]:
    return get_runtime(tunnel_id)
