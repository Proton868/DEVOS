"""Structured audit trail logging for DevOS.

Emits machine-parseable JSON log lines AND durable AuditLogger records.
Never logs secrets, private keys, passphrases, or raw credential material.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

from governance.audit import AuditEventType, AuditLogger, get_audit_logger

logger = logging.getLogger("devos.structured_audit")

# Request/job correlation (set by API/worker middleware when available)
_trace_id: ContextVar[str] = ContextVar("devos_trace_id", default="")
_actor_id: ContextVar[str] = ContextVar("devos_actor_id", default="")
_tenant_id: ContextVar[str] = ContextVar("devos_tenant_id", default="")

_SENSITIVE_KEYS = frozenset({
    "password", "passphrase", "private_key", "secret", "token", "api_key",
    "authorization", "credential", "client_secret", "ssh_key", "pem",
})


def set_audit_context(*, trace_id: str = "", actor_id: str = "", tenant_id: str = "") -> None:
    if trace_id:
        _trace_id.set(trace_id)
    if actor_id:
        _actor_id.set(actor_id)
    if tenant_id:
        _tenant_id.set(tenant_id)


def clear_audit_context() -> None:
    _trace_id.set("")
    _actor_id.set("")
    _tenant_id.set("")


def _scrub(obj: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        try:
            from governance.ssh_credentials import scrub_ssh_secrets_from_text
            return scrub_ssh_secrets_from_text(obj)[:2000]
        except Exception:
            lower = obj.lower()
            if any(m in lower for m in ("begin private", "begin openssh", "bearer ")):
                return "[REDACTED]"
            return obj[:2000]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _SENSITIVE_KEYS or any(s in kl for s in ("password", "secret", "private", "token", "passphrase")):
                out[k] = "[REDACTED]"
            else:
                out[k] = _scrub(v, depth=depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_scrub(x, depth=depth + 1) for x in obj[:50]]
    return str(obj)[:500]


def emit_audit_event(
    *,
    action: str,
    result: str,
    event_type: Any = AuditEventType.EXECUTION,
    actor_id: str = "",
    tenant_id: str = "",
    resource: str = "",
    resource_id: str = "",
    details: Optional[dict] = None,
    evidence_id: str = "",
    job_id: str = "",
    connection_id: str = "",
    host_identity: Optional[dict] = None,
    duration_ms: Optional[int] = None,
    persist: bool = True,
) -> dict:
    """Emit one structured audit event. Returns the public record (scrubbed)."""
    event_id = uuid.uuid4().hex
    ts = datetime.now(timezone.utc).isoformat()
    actor = actor_id or _actor_id.get() or ""
    tenant = tenant_id or _tenant_id.get() or ""
    trace = _trace_id.get() or job_id or ""

    detail_payload = dict(details or {})
    if evidence_id:
        detail_payload["evidence_id"] = evidence_id
    if job_id:
        detail_payload["job_id"] = job_id
    if connection_id:
        detail_payload["connection_id"] = connection_id
    if host_identity:
        # fingerprints only — never keys
        hi = {
            "fingerprint_sha256": (host_identity or {}).get("fingerprint_sha256"),
            "key_type": (host_identity or {}).get("key_type"),
            "state": (host_identity or {}).get("state") or (host_identity or {}).get("trust_state"),
        }
        detail_payload["host_identity"] = {k: v for k, v in hi.items() if v}
    if duration_ms is not None:
        detail_payload["duration_ms"] = duration_ms

    scrubbed_details = _scrub(detail_payload)

    record = {
        "event_id": event_id,
        "ts": ts,
        "event_type": event_type.value if isinstance(event_type, AuditEventType) else str(event_type),
        "action": action,
        "result": result,
        "actor_id": actor,
        "tenant_id": tenant,
        "resource": resource,
        "resource_id": resource_id,
        "trace_id": trace,
        "details": scrubbed_details,
    }

    # Structured log line (JSON) for aggregation systems
    try:
        logger.info("audit_event %s", json.dumps(record, default=str, separators=(",", ":")))
    except Exception:
        logger.info("audit_event action=%s result=%s resource=%s", action, result, resource)

    if persist:
        try:
            get_audit_logger().log(
                event_type=event_type,
                actor_id=actor,
                tenant_id=tenant,
                action=action,
                resource=resource,
                resource_id=resource_id,
                result=result,
                evidence=evidence_id,
                details=scrubbed_details,
                user_id=actor or None,
            )
        except Exception as e:
            logger.warning("audit_persist_failed action=%s err=%s", action, type(e).__name__)

    return record


# --- SSH-specific convenience emitters ---

def audit_ssh_exec(
    *,
    actor_id: str,
    connection_id: str,
    command: str,
    status: str,
    risk_class: str = "",
    evidence_id: str = "",
    host_identity: Optional[dict] = None,
    duration_ms: int = 0,
    tenant_id: str = "",
) -> dict:
    return emit_audit_event(
        event_type=AuditEventType.EXECUTION,
        action="ssh.exec",
        result=status,
        actor_id=actor_id,
        tenant_id=tenant_id,
        resource="ssh_connection",
        resource_id=connection_id,
        connection_id=connection_id,
        evidence_id=evidence_id,
        host_identity=host_identity,
        duration_ms=duration_ms,
        details={
            "command_preview": (command or "")[:200],
            "risk_class": risk_class,
        },
    )


def audit_ssh_credential_access(
    *,
    actor_id: str,
    credential_ref_id: str,
    result: str,
    tenant_id: str = "",
) -> dict:
    return emit_audit_event(
        event_type=AuditEventType.AUTH,
        action="ssh.credential.resolve",
        result=result,
        actor_id=actor_id,
        tenant_id=tenant_id,
        resource="ssh_credential",
        resource_id=credential_ref_id,
        details={},  # never include material
    )


def audit_ssh_host_verify(
    *,
    actor_id: str,
    host_identity_id: str,
    state: str,
    result: str,
    fingerprint_sha256: str = "",
    tenant_id: str = "",
) -> dict:
    return emit_audit_event(
        event_type=AuditEventType.GOVERNANCE,
        action="ssh.host_verify",
        result=result,
        actor_id=actor_id,
        tenant_id=tenant_id,
        resource="ssh_host",
        resource_id=host_identity_id,
        details={"state": state, "fingerprint_sha256": fingerprint_sha256},
    )


def audit_ssh_cancel(
    *,
    actor_id: str,
    job_id: str,
    reason: str = "",
    tenant_id: str = "",
) -> dict:
    return emit_audit_event(
        event_type=AuditEventType.AGENT,
        action="ssh.cancel",
        result="cancelled",
        actor_id=actor_id,
        tenant_id=tenant_id,
        resource="ssh_job",
        resource_id=job_id,
        job_id=job_id,
        details={"reason": reason, "success": False},
    )


def audit_ssh_diagnostic(
    *,
    actor_id: str,
    connection_id: str,
    capability: str,
    status: str,
    evidence_id: str = "",
    duration_ms: int = 0,
    tenant_id: str = "",
) -> dict:
    return emit_audit_event(
        event_type=AuditEventType.EXECUTION,
        action="ssh.diagnostic",
        result=status,
        actor_id=actor_id,
        tenant_id=tenant_id,
        resource="ssh_connection",
        resource_id=connection_id,
        connection_id=connection_id,
        evidence_id=evidence_id,
        duration_ms=duration_ms,
        details={"capability": capability},
    )


def audit_ssh_transfer(
    *,
    actor_id: str,
    connection_id: str,
    op: str,
    status: str,
    transfer_id: str = "",
    remote_path: str = "",
    bytes_transferred: int = 0,
    tenant_id: str = "",
) -> dict:
    return emit_audit_event(
        event_type=AuditEventType.EXECUTION,
        action="ssh.transfer",
        result=status,
        actor_id=actor_id,
        tenant_id=tenant_id,
        resource="ssh_connection",
        resource_id=connection_id,
        connection_id=connection_id,
        job_id=transfer_id,
        details={
            "op": op,
            "remote_path": (remote_path or "")[:200],
            "bytes_transferred": bytes_transferred,
        },
    )
