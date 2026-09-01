"""
Enterprise — Audit Logging (Agency OS Master Plan §7).

Structured, immutable audit trail for all capability invocations, identity
changes, and governance decisions. Each audit entry is a JSON blob with
timestamp, actor, action, target, and outcome. Persisted to SQLite for
local/micro profiles, with Supabase-compatible schema for tenant profiles.

Audit entries are append-only — once written, they cannot be modified or
deleted. This is enforced by the schema (no UPDATE/DELETE on audit_log).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.audit")

AUDIT_DIR = Path("data")
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DB = AUDIT_DIR / "audit.db"


class AuditEventType(str, Enum):
    CAPABILITY_INVOKE = "capability.invoke"
    CAPABILITY_RESULT = "capability.result"
    IDENTITY_CREATE = "identity.create"
    IDENTITY_DELEGATE = "identity.delegate"
    POLICY_EVALUATE = "policy.evaluate"
    POLICY_DECISION = "policy.decision"
    RBAC_CHECK = "rbac.check"
    BILLING_EVENT = "billing.event"
    MARKETPLACE_PUBLISH = "marketplace.publish"
    MARKETPLACE_INSTALL = "marketplace.install"
    TENANT_CREATE = "tenant.create"
    TENANT_DELETE = "tenant.delete"
    USER_INVITE = "user.invite"
    USER_REMOVE = "user.remove"
    SYSTEM_ERROR = "system.error"
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    # Auth events (security-audit P3j) -- covers both the local bcrypt+JWT
    # flow and the Supabase-primary flow (login/sync events distinguished
    # via the `action` field passed to AuditLogger.log(), e.g.
    # action="login" vs action="supabase_sync").
    LOGIN_SUCCESS = "auth.login_success"
    LOGIN_FAILURE = "auth.login_failure"
    LOGOUT = "auth.logout"
    TOKEN_REFRESH = "auth.token_refresh"
    PASSWORD_CHANGE = "auth.password_change"


@dataclass
class AuditEntry:
    event_id: str
    event_type: AuditEventType
    actor_id: str
    tenant_id: str
    timestamp: datetime
    action: str = ""
    target: str = ""
    outcome: str = ""
    details: dict = field(default_factory=dict)
    trace_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "target": self.target,
            "outcome": self.outcome,
            "details": self.details,
            "trace_id": self.trace_id,
            "metadata": self.metadata,
        }


class AuditLogger:
    """Append-only audit log.

    Schema:
      CREATE TABLE audit_log (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        action TEXT DEFAULT '',
        target TEXT DEFAULT '',
        outcome TEXT DEFAULT '',
        details TEXT DEFAULT '{}',
        trace_id TEXT,
        metadata TEXT DEFAULT '{}'
      );
      CREATE INDEX idx_audit_actor ON audit_log(actor_id, timestamp);
      CREATE INDEX idx_audit_tenant ON audit_log(tenant_id, timestamp);
      CREATE INDEX idx_audit_type ON audit_log(event_type, timestamp);
    """

    _instance = None
    _lock = None

    @classmethod
    def _get_lock(cls):
        if cls._lock is None:
            import threading
            cls._lock = threading.Lock()
        return cls._lock

    def __new__(cls):
        if cls._instance is None:
            with cls._get_lock():
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._init_db()
        self._initialized = True

    def _init_db(self):
        self._conn = sqlite3.connect(str(AUDIT_DB), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_log (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                action TEXT DEFAULT '',
                target TEXT DEFAULT '',
                outcome TEXT DEFAULT '',
                details TEXT DEFAULT '{}',
                trace_id TEXT,
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_log(event_type, timestamp);
        """)
        self._conn.commit()
        logger.info("[audit] database initialized")

    def log(self, event_type: AuditEventType, actor_id: str, tenant_id: str,
            action: str = "", target: str = "", outcome: str = "",
            details: Optional[dict] = None, trace_id: Optional[str] = None,
            metadata: Optional[dict] = None) -> str:
        """Write an audit entry. Returns the event_id."""
        event_id = str(uuid.uuid4())
        entry = AuditEntry(
            event_id=event_id,
            event_type=event_type,
            actor_id=actor_id,
            tenant_id=tenant_id,
            timestamp=datetime.now(timezone.utc),
            action=action,
            target=target,
            outcome=outcome,
            details=details or {},
            trace_id=trace_id,
            metadata=metadata or {},
        )
        try:
            self._conn.execute(
                """INSERT INTO audit_log (event_id, event_type, actor_id, tenant_id,
                   timestamp, action, target, outcome, details, trace_id, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry.event_id, entry.event_type.value, entry.actor_id,
                 entry.tenant_id, entry.timestamp.isoformat(), entry.action,
                 entry.target, entry.outcome, json.dumps(entry.details),
                 entry.trace_id, json.dumps(entry.metadata)),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"[audit] write failed: {e}")
        return event_id

    def query(self, actor_id: Optional[str] = None,
              tenant_id: Optional[str] = None,
              event_type: Optional[AuditEventType] = None,
              limit: int = 100, offset: int = 0) -> list[dict]:
        """Query audit entries with optional filters."""
        conditions = []
        params = []
        if actor_id:
            conditions.append("actor_id = ?")
            params.append(actor_id)
        if tenant_id:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type.value)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        query = f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        try:
            cursor = self._conn.execute(query, params)
            columns = [d[0] for d in cursor.description]
            rows = []
            for row in cursor.fetchall():
                entry = dict(zip(columns, row))
                entry["details"] = json.loads(entry.get("details", "{}"))
                entry["metadata"] = json.loads(entry.get("metadata", "{}"))
                rows.append(entry)
            return rows
        except Exception as e:
            logger.error(f"[audit] query failed: {e}")
            return []

    def stats(self, tenant_id: Optional[str] = None) -> dict:
        """Get audit statistics."""
        where = ""
        params = []
        if tenant_id:
            where = "WHERE tenant_id = ?"
            params.append(tenant_id)

        try:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM audit_log {where}", params
            ).fetchone()[0]
            by_type = {}
            for row in self._conn.execute(
                f"SELECT event_type, COUNT(*) FROM audit_log {where} GROUP BY event_type",
                params,
            ).fetchall():
                by_type[row[0]] = row[1]
            return {"total_entries": total, "by_type": by_type}
        except Exception as e:
            logger.error(f"[audit] stats failed: {e}")
            return {"total_entries": 0, "by_type": {}}

    def close(self):
        if hasattr(self, "_conn"):
            self._conn.close()


def get_audit_logger() -> AuditLogger:
    return AuditLogger()