"""SQLite durable store for shares, runtimes, deployments, tunnels — uses existing DB path conventions."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_DB_PATH = Path(os.environ.get("DEVOS_DURABLE_DB", "data/delivery_durable.sqlite3"))


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_store() -> None:
    with _LOCK:
        c = _conn()
        try:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtimes (
                  runtime_id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  status TEXT,
                  pid INTEGER,
                  port INTEGER,
                  command TEXT,
                  cwd TEXT,
                  app_type TEXT,
                  revision TEXT,
                  isolation_mode TEXT,
                  last_error TEXT,
                  created_at REAL,
                  started_at REAL,
                  stopped_at REAL,
                  meta_json TEXT
                );
                CREATE TABLE IF NOT EXISTS shares (
                  share_id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  path TEXT NOT NULL,
                  permission TEXT,
                  status TEXT,
                  revision_hash TEXT,
                  created_at REAL,
                  expires_at REAL,
                  revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS deployments (
                  deployment_id TEXT PRIMARY KEY,
                  user_id TEXT,
                  project_id TEXT,
                  provider TEXT,
                  status TEXT,
                  provider_deployment_id TEXT,
                  url TEXT,
                  verification TEXT,
                  error TEXT,
                  revision TEXT,
                  evidence_json TEXT,
                  requested_at REAL,
                  started_at REAL,
                  completed_at REAL
                );
                CREATE TABLE IF NOT EXISTS tunnels (
                  tunnel_id TEXT PRIMARY KEY,
                  user_id TEXT,
                  project_id TEXT,
                  status TEXT,
                  hostname TEXT,
                  local_port INTEGER,
                  pid INTEGER,
                  config_path TEXT,
                  last_error TEXT,
                  created_at REAL,
                  started_at REAL,
                  stopped_at REAL,
                  meta_json TEXT
                );
                CREATE TABLE IF NOT EXISTS runtime_logs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  runtime_id TEXT NOT NULL,
                  ts REAL NOT NULL,
                  stream TEXT,
                  line TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_logs_rt ON runtime_logs(runtime_id, id);
                """
            )
            c.commit()
        finally:
            c.close()


init_store()


def upsert_runtime(rec: dict) -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO runtimes(runtime_id,user_id,project_id,status,pid,port,command,cwd,app_type,revision,isolation_mode,last_error,created_at,started_at,stopped_at,meta_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(runtime_id) DO UPDATE SET
                     status=excluded.status,pid=excluded.pid,port=excluded.port,command=excluded.command,
                     last_error=excluded.last_error,started_at=excluded.started_at,stopped_at=excluded.stopped_at,
                     isolation_mode=excluded.isolation_mode,meta_json=excluded.meta_json
                """,
                (
                    rec["runtime_id"], rec["user_id"], rec["project_id"], rec.get("status"),
                    rec.get("pid"), rec.get("port"), rec.get("command"), rec.get("cwd"),
                    rec.get("app_type"), rec.get("revision"), rec.get("isolation_mode"),
                    rec.get("last_error"), rec.get("created_at", time.time()),
                    rec.get("started_at"), rec.get("stopped_at"),
                    json.dumps(rec.get("meta") or {}),
                ),
            )
            c.commit()
        finally:
            c.close()


def get_runtime(runtime_id: str) -> Optional[dict]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM runtimes WHERE runtime_id=?", (runtime_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def list_runtimes(project_id: str) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute("SELECT * FROM runtimes WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
            return [dict(x) for x in rows]
        finally:
            c.close()


def append_log(runtime_id: str, stream: str, line: str, *, max_lines: int = 5000) -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO runtime_logs(runtime_id,ts,stream,line) VALUES(?,?,?,?)",
                (runtime_id, time.time(), stream, line[:4000]),
            )
            # bound retention
            c.execute(
                """DELETE FROM runtime_logs WHERE runtime_id=? AND id NOT IN (
                     SELECT id FROM runtime_logs WHERE runtime_id=? ORDER BY id DESC LIMIT ?
                   )""",
                (runtime_id, runtime_id, max_lines),
            )
            c.commit()
        finally:
            c.close()


def read_logs(runtime_id: str, after_id: int = 0, limit: int = 500) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT id,ts,stream,line FROM runtime_logs WHERE runtime_id=? AND id>? ORDER BY id ASC LIMIT ?",
                (runtime_id, after_id, limit),
            ).fetchall()
            return [dict(x) for x in rows]
        finally:
            c.close()


def save_share(rec: dict) -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO shares(share_id,user_id,project_id,path,permission,status,revision_hash,created_at,expires_at,revoked_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(share_id) DO UPDATE SET status=excluded.status,revoked_at=excluded.revoked_at""",
                (
                    rec["share_id"], rec["user_id"], rec["project_id"], rec["path"],
                    rec.get("permission"), rec.get("status"), rec.get("revision_hash"),
                    rec.get("created_at", time.time()), rec.get("expires_at"), rec.get("revoked_at"),
                ),
            )
            c.commit()
        finally:
            c.close()


def get_share_db(share_id: str) -> Optional[dict]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM shares WHERE share_id=?", (share_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def save_deployment(rec: dict) -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO deployments(deployment_id,user_id,project_id,provider,status,provider_deployment_id,url,verification,error,revision,evidence_json,requested_at,started_at,completed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(deployment_id) DO UPDATE SET
                     status=excluded.status,provider_deployment_id=excluded.provider_deployment_id,
                     url=excluded.url,verification=excluded.verification,error=excluded.error,
                     evidence_json=excluded.evidence_json,completed_at=excluded.completed_at,started_at=excluded.started_at
                """,
                (
                    rec["deployment_id"], rec.get("user_id"), rec.get("project_id"), rec.get("provider"),
                    rec.get("status"), rec.get("provider_deployment_id"), rec.get("url"),
                    rec.get("verification"), rec.get("error"), rec.get("revision"),
                    json.dumps(rec.get("evidence") or {}),
                    rec.get("requested_at", time.time()), rec.get("started_at"), rec.get("completed_at"),
                ),
            )
            c.commit()
        finally:
            c.close()


def get_deployment(deployment_id: str) -> Optional[dict]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM deployments WHERE deployment_id=?", (deployment_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def save_tunnel(rec: dict) -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO tunnels(tunnel_id,user_id,project_id,status,hostname,local_port,pid,config_path,last_error,created_at,started_at,stopped_at,meta_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(tunnel_id) DO UPDATE SET
                     status=excluded.status,hostname=excluded.hostname,local_port=excluded.local_port,
                     pid=excluded.pid,last_error=excluded.last_error,started_at=excluded.started_at,
                     stopped_at=excluded.stopped_at,meta_json=excluded.meta_json
                """,
                (
                    rec["tunnel_id"], rec.get("user_id"), rec.get("project_id"), rec.get("status"),
                    rec.get("hostname"), rec.get("local_port"), rec.get("pid"), rec.get("config_path"),
                    rec.get("last_error"), rec.get("created_at", time.time()),
                    rec.get("started_at"), rec.get("stopped_at"), json.dumps(rec.get("meta") or {}),
                ),
            )
            c.commit()
        finally:
            c.close()


def get_tunnel(tunnel_id: str) -> Optional[dict]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM tunnels WHERE tunnel_id=?", (tunnel_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}" if prefix else uuid.uuid4().hex
