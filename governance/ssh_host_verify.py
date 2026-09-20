"""Production-grade SSH host identity verification.

States:
  NEW_HOST       — no known fingerprint for this owner/host
  KNOWN_HOST     — fingerprint matches pinned/tofu record
  CHANGED_HOST   — fingerprint differs from pinned (HARD STOP)
  REVOKED_HOST   — known host explicitly revoked/invalid

Agents must NEVER silently approve NEW or CHANGED hosts.
StrictHostKeyChecking=no is not implemented and must not be added.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("devos.ssh_host_verify")


class HostTrustState(str, Enum):
    NEW_HOST = "new_host"
    KNOWN_HOST = "known_host"
    CHANGED_HOST = "changed_host"
    REVOKED_HOST = "revoked_host"


class HostVerifyError(Exception):
    def __init__(self, code: str, state: Optional[HostTrustState] = None):
        self.code = str(code)[:64]
        self.state = state
        super().__init__(self.code)


class HostKeyChangedError(HostVerifyError):
    def __init__(self):
        super().__init__("host_key_changed", HostTrustState.CHANGED_HOST)


class HostKeyRevokedError(HostVerifyError):
    def __init__(self):
        super().__init__("host_key_revoked", HostTrustState.REVOKED_HOST)


class HostKeyUnknownError(HostVerifyError):
    """Agent path must pause for user approval; human may approve TOFU."""

    def __init__(self):
        super().__init__("host_key_unknown", HostTrustState.NEW_HOST)


class HostKeyApprovalRequired(HostVerifyError):
    def __init__(self, state: HostTrustState):
        super().__init__("host_key_approval_required", state)


def normalize_fingerprint_sha256(fp: str) -> str:
    """Normalize to SHA256:<base64> form."""
    fp = (fp or "").strip()
    if not fp:
        return ""
    if fp.upper().startswith("SHA256:"):
        return "SHA256:" + fp.split(":", 1)[1].strip()
    # bare base64
    if re.match(r"^[A-Za-z0-9+/=]+$", fp):
        return f"SHA256:{fp}"
    return fp


def fingerprint_from_public_key_blob(key_type: str, public_key_bytes: bytes) -> str:
    """Compute SHA256 fingerprint from raw public key blob (OpenSSH-style)."""
    digest = hashlib.sha256(public_key_bytes).digest()
    import base64

    b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{b64}"


def fingerprint_from_openssh_line(line: str) -> tuple[str, str]:
    """Parse 'ssh-ed25519 AAAA... comment' → (key_type, SHA256:...)."""
    parts = (line or "").strip().split()
    if len(parts) < 2:
        raise HostVerifyError("invalid_public_key_line")
    key_type, b64 = parts[0], parts[1]
    import base64

    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        raise HostVerifyError("invalid_public_key_encoding") from e
    return key_type, fingerprint_from_public_key_blob(key_type, raw)


@dataclass
class HostVerificationResult:
    state: HostTrustState
    host_identity_id: str
    hostname: str
    port: int
    presented_key_type: str
    presented_fingerprint: str
    known_fingerprint: Optional[str] = None
    known_host_id: Optional[str] = None
    allowed: bool = False
    requires_human_approval: bool = False
    hard_stop: bool = False
    message: str = ""

    def to_evidence(self) -> dict:
        """Host identity block for execution evidence — no private keys."""
        return {
            "host_identity_id": self.host_identity_id,
            "hostname": self.hostname,
            "port": self.port,
            "trust_state": self.state.value,
            "key_type": self.presented_key_type,
            "fingerprint_sha256": self.presented_fingerprint,
            "known_fingerprint_sha256": self.known_fingerprint,
            "allowed": self.allowed,
            "requires_human_approval": self.requires_human_approval,
            "hard_stop": self.hard_stop,
        }

    def to_public_dict(self) -> dict:
        return self.to_evidence()


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def verify_host_key(
    *,
    owner_id: str,
    host_identity_id: str,
    hostname: str,
    port: int,
    presented_key_type: str,
    presented_fingerprint: str,
    actor: str = "user",  # user | agent
    auto_approve_new: bool = False,  # ONLY for explicit human TOFU path
) -> HostVerificationResult:
    """Verify presented host key against durable known_hosts.

    actor=agent:
      NEW → requires_human_approval, not allowed
      CHANGED → hard_stop, not allowed
      REVOKED → hard_stop
      KNOWN → allowed
    actor=user:
      NEW → requires_human_approval unless auto_approve_new (explicit TOFU)
      CHANGED → hard_stop (user must re-pin after investigation)
      REVOKED → hard_stop
      KNOWN → allowed
    """
    from core.database import AsyncSessionLocal, SshKnownHost
    from sqlalchemy import select

    fp = normalize_fingerprint_sha256(presented_fingerprint)
    key_type = (presented_key_type or "").strip()
    if not fp or not key_type:
        raise HostVerifyError("missing_host_key_presentation")

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(SshKnownHost).where(
                SshKnownHost.owner_id == owner_id,
                SshKnownHost.host_identity_id == host_identity_id,
            )
        )
        rows = list(r.scalars().all())

    revoked = [x for x in rows if (x.trust_state or "") == "revoked"]
    active = [x for x in rows if (x.trust_state or "") in ("pinned", "tofu")]

    if revoked and any(
        normalize_fingerprint_sha256(x.fingerprint_sha256) == fp for x in revoked
    ):
        result = HostVerificationResult(
            state=HostTrustState.REVOKED_HOST,
            host_identity_id=host_identity_id,
            hostname=hostname,
            port=port,
            presented_key_type=key_type,
            presented_fingerprint=fp,
            known_fingerprint=fp,
            known_host_id=revoked[0].id,
            allowed=False,
            hard_stop=True,
            message="host key is revoked",
        )
        logger.warning(
            "ssh_host_revoked owner=%s host=%s fp=%s", owner_id, hostname, fp
        )
        return result

    if not active:
        # NEW HOST
        allowed = bool(auto_approve_new and actor == "user")
        result = HostVerificationResult(
            state=HostTrustState.NEW_HOST,
            host_identity_id=host_identity_id,
            hostname=hostname,
            port=port,
            presented_key_type=key_type,
            presented_fingerprint=fp,
            allowed=allowed,
            requires_human_approval=not allowed,
            hard_stop=False,
            message="unknown host key — user approval required",
        )
        logger.info(
            "ssh_host_new owner=%s host=%s fp=%s actor=%s allowed=%s",
            owner_id, hostname, fp, actor, allowed,
        )
        return result

    # Match any active fingerprint
    for kh in active:
        known_fp = normalize_fingerprint_sha256(kh.fingerprint_sha256)
        if known_fp == fp and (not kh.key_type or kh.key_type == key_type or True):
            # Prefer same key_type match when multiple
            if kh.key_type and kh.key_type != key_type:
                continue
            return HostVerificationResult(
                state=HostTrustState.KNOWN_HOST,
                host_identity_id=host_identity_id,
                hostname=hostname,
                port=port,
                presented_key_type=key_type,
                presented_fingerprint=fp,
                known_fingerprint=known_fp,
                known_host_id=kh.id,
                allowed=True,
                message="host key matches known host",
            )

    # Same host, different fingerprint → CHANGED (MITM risk)
    primary = active[0]
    result = HostVerificationResult(
        state=HostTrustState.CHANGED_HOST,
        host_identity_id=host_identity_id,
        hostname=hostname,
        port=port,
        presented_key_type=key_type,
        presented_fingerprint=fp,
        known_fingerprint=normalize_fingerprint_sha256(primary.fingerprint_sha256),
        known_host_id=primary.id,
        allowed=False,
        hard_stop=True,
        requires_human_approval=True,
        message="REMOTE HOST IDENTIFICATION HAS CHANGED",
    )
    logger.error(
        "ssh_host_changed owner=%s host=%s known=%s presented=%s",
        owner_id,
        hostname,
        result.known_fingerprint,
        fp,
    )
    return result


async def approve_new_host(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    host_identity_id: str,
    key_type: str,
    fingerprint_sha256: str,
    public_key: Optional[str] = None,
    trust_state: str = "tofu",  # tofu | pinned
) -> dict:
    """Human-only: record NEW host key after explicit approval."""
    from core.database import AsyncSessionLocal, SshKnownHost, SshHostIdentity, gen_id

    if trust_state not in ("tofu", "pinned"):
        raise HostVerifyError("invalid_trust_state")

    fp = normalize_fingerprint_sha256(fingerprint_sha256)
    async with AsyncSessionLocal() as db:
        host = await db.get(SshHostIdentity, host_identity_id)
        if not host or host.owner_id != owner_id:
            raise HostVerifyError("host_not_found")

        # Refuse if active different key exists → must use replace flow
        from sqlalchemy import select

        r = await db.execute(
            select(SshKnownHost).where(
                SshKnownHost.owner_id == owner_id,
                SshKnownHost.host_identity_id == host_identity_id,
                SshKnownHost.trust_state.in_(("pinned", "tofu")),
            )
        )
        existing = list(r.scalars().all())
        for ex in existing:
            if normalize_fingerprint_sha256(ex.fingerprint_sha256) != fp:
                raise HostKeyChangedError()

        row = SshKnownHost(
            id=gen_id(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            host_identity_id=host_identity_id,
            key_type=key_type,
            fingerprint_sha256=fp,
            public_key=public_key,
            trust_state=trust_state,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(row)
        await db.commit()
        logger.info(
            "ssh_host_approved owner=%s host_id=%s fp=%s state=%s",
            owner_id, host_identity_id, fp, trust_state,
        )
        return {
            "id": row.id,
            "host_identity_id": host_identity_id,
            "key_type": key_type,
            "fingerprint_sha256": fp,
            "trust_state": trust_state,
        }


async def revoke_known_host(*, owner_id: str, known_host_id: str) -> dict:
    from core.database import AsyncSessionLocal, SshKnownHost

    async with AsyncSessionLocal() as db:
        row = await db.get(SshKnownHost, known_host_id)
        if not row or row.owner_id != owner_id:
            raise HostVerifyError("known_host_not_found")
        row.trust_state = "revoked"
        row.updated_at = _utcnow()
        await db.commit()
        logger.info("ssh_host_revoked_id owner=%s id=%s", owner_id, known_host_id)
        return {
            "id": row.id,
            "trust_state": "revoked",
            "fingerprint_sha256": row.fingerprint_sha256,
        }


async def replace_changed_host_key(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    host_identity_id: str,
    key_type: str,
    fingerprint_sha256: str,
    public_key: Optional[str] = None,
    acknowledge_mitm_risk: bool = False,
) -> dict:
    """Human-only recovery after CHANGED_HOST. Requires explicit ack."""
    if not acknowledge_mitm_risk:
        raise HostVerifyError("mitm_ack_required")

    from core.database import AsyncSessionLocal, SshKnownHost, gen_id
    from sqlalchemy import select

    fp = normalize_fingerprint_sha256(fingerprint_sha256)
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(SshKnownHost).where(
                SshKnownHost.owner_id == owner_id,
                SshKnownHost.host_identity_id == host_identity_id,
                SshKnownHost.trust_state.in_(("pinned", "tofu")),
            )
        )
        for old in r.scalars().all():
            old.trust_state = "revoked"
            old.updated_at = _utcnow()
        row = SshKnownHost(
            id=gen_id(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            host_identity_id=host_identity_id,
            key_type=key_type,
            fingerprint_sha256=fp,
            public_key=public_key,
            trust_state="pinned",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(row)
        await db.commit()
        logger.warning(
            "ssh_host_key_replaced owner=%s host_id=%s new_fp=%s",
            owner_id, host_identity_id, fp,
        )
        return {
            "id": row.id,
            "fingerprint_sha256": fp,
            "trust_state": "pinned",
            "replaced": True,
        }


def assert_connect_allowed(result: HostVerificationResult, *, actor: str = "user") -> None:
    """Raise if transport must not proceed."""
    if result.state == HostTrustState.CHANGED_HOST:
        raise HostKeyChangedError()
    if result.state == HostTrustState.REVOKED_HOST:
        raise HostKeyRevokedError()
    if result.state == HostTrustState.NEW_HOST and not result.allowed:
        if actor == "agent":
            raise HostKeyApprovalRequired(HostTrustState.NEW_HOST)
        raise HostKeyUnknownError()
    if not result.allowed:
        raise HostVerifyError("host_not_trusted", result.state)
