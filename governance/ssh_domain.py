"""SSH connection domain services — owner-scoped, no credential material in records."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("devos.ssh_domain")


class SshDomainError(Exception):
    pass


class SshAccessDenied(SshDomainError):
    pass


class SshConnectionRevoked(SshDomainError):
    pass


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _host_key(hostname: str, port: int) -> str:
    return f"{(hostname or '').strip().lower()}|{int(port)}"


def connection_to_public(row, host=None) -> dict:
    return {
        "id": row.id,
        "label": row.label,
        "username": row.username,
        "auth_method": row.auth_method,
        "credential_ref_id": row.credential_ref_id,
        "host_identity_id": row.host_identity_id,
        "hostname": getattr(host, "hostname", None),
        "port": getattr(host, "port", None),
        "agent_forwarding": bool(row.agent_forwarding),
        "disabled": bool(row.disabled),
        "revoked": bool(row.revoked),
        "health_status": row.health_status,
        "workspace_id": row.workspace_id,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        # no private keys, no secret ciphertext
    }


def session_to_public(row) -> dict:
    return {
        "id": row.id,
        "connection_id": row.connection_id,
        "status": row.status,
        "mode": row.mode,
        "actor_id": row.actor_id,
        "agent_id": row.agent_id,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "evidence_id": row.evidence_id,
    }


def execution_to_public(row) -> dict:
    return {
        "id": row.id,
        "connection_id": row.connection_id,
        "session_id": row.session_id,
        "command": row.command,
        "status": row.status,
        "cancel_requested": bool(row.cancel_requested),
        "exit_code": row.exit_code,
        "stdout_ref": row.stdout_ref,
        "stderr_ref": row.stderr_ref,
        "evidence_id": row.evidence_id,
        "actor_id": row.actor_id,
        "agent_id": row.agent_id,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "operation_id": row.operation_id,
        "job_id": row.job_id,
    }


async def upsert_host_identity(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    hostname: str,
    port: int = 22,
) -> str:
    from core.database import AsyncSessionLocal, SshHostIdentity, gen_id
    from sqlalchemy import select

    hostname = (hostname or "").strip()
    if not hostname:
        raise SshDomainError("hostname_required")
    port = int(port or 22)
    hk = _host_key(hostname, port)
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(SshHostIdentity).where(
                SshHostIdentity.owner_id == owner_id,
                SshHostIdentity.host_key == hk,
            )
        )
        row = r.scalar_one_or_none()
        if row:
            return row.id
        row = SshHostIdentity(
            id=gen_id(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            hostname=hostname,
            port=port,
            host_key=hk,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(row)
        await db.commit()
        return row.id


async def create_connection(
    *,
    owner_id: str,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    label: str = "",
    hostname: str,
    port: int = 22,
    username: str,
    auth_method: str = "private_key",
    credential_ref_id: Optional[str] = None,
    agent_forwarding: bool = False,
    created_by: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    from governance.ssh_network_policy import assert_ssh_target_allowed
    assert_ssh_target_allowed(
        hostname, port, actor=(created_by or "user"),
    )
    from core.database import AsyncSessionLocal, SshConnection, SshCredentialRef, gen_id

    auth_method = (auth_method or "private_key").lower()
    if agent_forwarding and auth_method != "agent_forwarding":
        # Explicit flag only when method allows
        pass
    if auth_method == "agent_forwarding":
        agent_forwarding = True

    host_id = await upsert_host_identity(
        owner_id=owner_id, tenant_id=tenant_id, hostname=hostname, port=port,
    )

    async with AsyncSessionLocal() as db:
        if credential_ref_id:
            cred = await db.get(SshCredentialRef, credential_ref_id)
            if not cred or cred.owner_id != owner_id:
                raise SshAccessDenied("credential_not_found")
            if cred.revoked:
                raise SshConnectionRevoked("credential_revoked")

        row = SshConnection(
            id=gen_id(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            label=label or f"{username}@{hostname}",
            host_identity_id=host_id,
            username=username,
            auth_method=auth_method,
            credential_ref_id=credential_ref_id,
            agent_forwarding=bool(agent_forwarding),
            disabled=False,
            revoked=False,
            health_status="unknown",
            metadata_=dict(metadata or {}),
            created_by=created_by or owner_id,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        from core.database import SshHostIdentity
        host = await db.get(SshHostIdentity, host_id)
        logger.info("ssh_connection_created id=%s owner_id=%s host=%s", row.id, owner_id, hostname)
        return connection_to_public(row, host)


async def get_connection_for_owner(owner_id: str, connection_id: str):
    from core.database import AsyncSessionLocal, SshConnection, SshHostIdentity

    async with AsyncSessionLocal() as db:
        row = await db.get(SshConnection, connection_id)
        if not row or row.owner_id != owner_id:
            raise SshAccessDenied("connection_not_found")
        host = await db.get(SshHostIdentity, row.host_identity_id)
        return row, host


async def assert_connection_usable(owner_id: str, connection_id: str):
    row, host = await get_connection_for_owner(owner_id, connection_id)
    if row.revoked or row.disabled:
        raise SshConnectionRevoked("connection_revoked_or_disabled")
    return row, host


async def revoke_connection(owner_id: str, connection_id: str) -> dict:
    from core.database import AsyncSessionLocal, SshConnection, SshHostIdentity

    async with AsyncSessionLocal() as db:
        row = await db.get(SshConnection, connection_id)
        if not row or row.owner_id != owner_id:
            raise SshAccessDenied("connection_not_found")
        row.revoked = True
        row.disabled = True
        row.updated_at = _utcnow()
        await db.commit()
        host = await db.get(SshHostIdentity, row.host_identity_id)
        return connection_to_public(row, host)


async def pin_known_host(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    host_identity_id: str,
    key_type: str,
    fingerprint_sha256: str,
    public_key: Optional[str] = None,
) -> dict:
    from core.database import AsyncSessionLocal, SshKnownHost, SshHostIdentity, gen_id

    async with AsyncSessionLocal() as db:
        host = await db.get(SshHostIdentity, host_identity_id)
        if not host or host.owner_id != owner_id:
            raise SshAccessDenied("host_not_found")
        row = SshKnownHost(
            id=gen_id(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            host_identity_id=host_identity_id,
            key_type=key_type,
            fingerprint_sha256=fingerprint_sha256,
            public_key=public_key,
            trust_state="pinned",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(row)
        await db.commit()
        return {
            "id": row.id,
            "host_identity_id": host_identity_id,
            "key_type": key_type,
            "fingerprint_sha256": fingerprint_sha256,
            "trust_state": "pinned",
        }


async def create_session(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    connection_id: str,
    mode: str = "interactive",
    actor_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> dict:
    from core.database import AsyncSessionLocal, SshSession, gen_id

    row, host = await assert_connection_usable(owner_id, connection_id)
    async with AsyncSessionLocal() as db:
        sess = SshSession(
            id=gen_id(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            connection_id=connection_id,
            host_identity_id=row.host_identity_id,
            status="pending",
            mode=mode,
            actor_id=actor_id or owner_id,
            agent_id=agent_id,
            remote_username=row.username,
            started_at=_utcnow(),
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
        return session_to_public(sess)


async def create_execution_record(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    connection_id: str,
    command: str,
    session_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    operation_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> dict:
    from core.database import AsyncSessionLocal, SshExecutionRecord, gen_id
    from governance.ssh_credentials import scrub_ssh_secrets_from_text

    await assert_connection_usable(owner_id, connection_id)
    safe_cmd = scrub_ssh_secrets_from_text(command or "")[:8000]
    async with AsyncSessionLocal() as db:
        rec = SshExecutionRecord(
            id=gen_id(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            connection_id=connection_id,
            session_id=session_id,
            operation_id=operation_id,
            job_id=job_id,
            actor_id=actor_id or owner_id,
            agent_id=agent_id,
            command=safe_cmd,
            status="queued",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(rec)
        await db.commit()
        await db.refresh(rec)
        return execution_to_public(rec)
