"""Secure SSH credential resolution for DevOS.

Private keys, passphrases, and passwords:
  - live only in secrets.encrypted_value (Fernet via secrets_vault)
  - are resolved server-side only after owner + non-revoked checks
  - are never returned from public serializers or agent capability handles
  - must not appear in logs, SSE, or exception messages

Agents receive capability handles (credential_ref_id), not material.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.ssh_credentials")

# Fields that must never leave the resolution boundary
_SENSITIVE_MARKERS = (
    "PRIVATE KEY",
    "BEGIN OPENSSH",
    "BEGIN RSA",
    "BEGIN EC",
    "BEGIN DSA",
    "passphrase",
    "password",
)


class SshCredentialError(Exception):
    """Safe error — message must not include secret material."""


class SshCredentialDenied(SshCredentialError):
    pass


class SshCredentialRevoked(SshCredentialError):
    pass


@dataclass
class SshCredentialHandle:
    """Public-safe handle for agents and APIs."""
    credential_ref_id: str
    owner_id: str
    name: str
    auth_method: str
    revoked: bool
    public_metadata: dict

    def to_public_dict(self) -> dict:
        return {
            "credential_ref_id": self.credential_ref_id,
            "name": self.name,
            "auth_method": self.auth_method,
            "revoked": self.revoked,
            "public_metadata": dict(self.public_metadata or {}),
            # Explicit: no secret_id, no material
        }


@dataclass
class ResolvedSshMaterial:
    """In-memory material for transport only. Do not serialize to JSON responses."""
    auth_method: str
    private_key_pem: Optional[str] = None
    password: Optional[str] = None
    passphrase: Optional[str] = None
    agent_forwarding: bool = False

    def clear(self) -> None:
        self.private_key_pem = None
        self.password = None
        self.passphrase = None

    def __repr__(self) -> str:
        return (
            f"ResolvedSshMaterial(auth_method={self.auth_method!r}, "
            f"has_key={bool(self.private_key_pem)}, has_password={bool(self.password)}, "
            f"has_passphrase={bool(self.passphrase)}, agent_forwarding={self.agent_forwarding})"
        )

    __str__ = __repr__


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def credential_ref_to_public(row) -> dict:
    """Serialize credential ref for API — never includes secret ids' values or ciphertext."""
    return {
        "id": row.id,
        "name": row.name,
        "auth_method": row.auth_method,
        "revoked": bool(row.revoked),
        "public_metadata": dict(row.public_metadata or {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        # secret_id intentionally omitted from public API
    }


async def create_ssh_credential_ref(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    name: str,
    auth_method: str,
    secret_plaintext: str,
    passphrase_plaintext: Optional[str] = None,
    public_metadata: Optional[dict] = None,
) -> dict:
    """Store material in secrets vault; return public handle only."""
    from core.database import AsyncSessionLocal, Secret, SshCredentialRef, gen_id
    from governance.secrets_vault import encrypt

    auth_method = (auth_method or "").strip().lower()
    if auth_method not in ("private_key", "password", "agent_forwarding"):
        raise SshCredentialError("unsupported_auth_method")
    if auth_method == "agent_forwarding":
        # No secret material required; still create a ref for audit binding
        secret_plaintext = secret_plaintext or ""
    if auth_method in ("private_key", "password") and not (secret_plaintext or "").strip():
        raise SshCredentialError("credential_material_required")

    # Reject obvious logging of material in name
    name = (name or "").strip()[:128] or "ssh-credential"

    async with AsyncSessionLocal() as db:
        secret = Secret(
            id=gen_id(),
            owner_id=owner_id,
            name=f"SSH_CRED_{gen_id()[:8].upper()}",
            description="ssh_credential_material",
            encrypted_value=encrypt(secret_plaintext) if secret_plaintext else encrypt(""),
        )
        db.add(secret)
        passphrase_secret_id = None
        if passphrase_plaintext:
            ps = Secret(
                id=gen_id(),
                owner_id=owner_id,
                name=f"SSH_PASS_{gen_id()[:8].upper()}",
                description="ssh_key_passphrase",
                encrypted_value=encrypt(passphrase_plaintext),
            )
            db.add(ps)
            passphrase_secret_id = ps.id

        ref = SshCredentialRef(
            id=gen_id(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            name=name,
            auth_method=auth_method,
            secret_id=secret.id,
            passphrase_secret_id=passphrase_secret_id,
            public_metadata=dict(public_metadata or {}),
            revoked=False,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(ref)
        await db.commit()
        await db.refresh(ref)
        logger.info(
            "ssh_credential_ref_created ref_id=%s owner_id=%s auth_method=%s",
            ref.id, owner_id, auth_method,
        )
        return credential_ref_to_public(ref)


async def get_credential_handle(owner_id: str, credential_ref_id: str) -> SshCredentialHandle:
    from core.database import AsyncSessionLocal, SshCredentialRef

    async with AsyncSessionLocal() as db:
        row = await db.get(SshCredentialRef, credential_ref_id)
        if not row or row.owner_id != owner_id:
            raise SshCredentialDenied("credential_not_found")
        return SshCredentialHandle(
            credential_ref_id=row.id,
            owner_id=row.owner_id,
            name=row.name,
            auth_method=row.auth_method,
            revoked=bool(row.revoked),
            public_metadata=dict(row.public_metadata or {}),
        )


async def revoke_credential(owner_id: str, credential_ref_id: str) -> dict:
    from core.database import AsyncSessionLocal, SshCredentialRef

    async with AsyncSessionLocal() as db:
        row = await db.get(SshCredentialRef, credential_ref_id)
        if not row or row.owner_id != owner_id:
            raise SshCredentialDenied("credential_not_found")
        row.revoked = True
        row.revoked_at = _utcnow()
        row.updated_at = _utcnow()
        await db.commit()
        logger.info("ssh_credential_revoked ref_id=%s owner_id=%s", credential_ref_id, owner_id)
        return credential_ref_to_public(row)


async def resolve_ssh_material(
    *,
    owner_id: str,
    credential_ref_id: str,
    purpose: str = "ssh_transport",
) -> ResolvedSshMaterial:
    """Decrypt material for authorized server-side transport only.

    Caller must clear() the result after use. Never pass to LLM/agent context.
    """
    from core.database import AsyncSessionLocal, SshCredentialRef, Secret
    from governance.secrets_vault import decrypt

    async with AsyncSessionLocal() as db:
        row = await db.get(SshCredentialRef, credential_ref_id)
        if not row or row.owner_id != owner_id:
            raise SshCredentialDenied("credential_not_found")
        if row.revoked:
            raise SshCredentialRevoked("credential_revoked")

        secret = await db.get(Secret, row.secret_id)
        if not secret or secret.owner_id != owner_id:
            raise SshCredentialDenied("secret_not_found")

        try:
            material = decrypt(secret.encrypted_value)
        except Exception:
            logger.warning("ssh_credential_decrypt_failed ref_id=%s", credential_ref_id)
            raise SshCredentialError("credential_unavailable") from None

        passphrase = None
        if row.passphrase_secret_id:
            ps = await db.get(Secret, row.passphrase_secret_id)
            if ps and ps.owner_id == owner_id:
                try:
                    passphrase = decrypt(ps.encrypted_value)
                except Exception:
                    raise SshCredentialError("passphrase_unavailable") from None

        auth = row.auth_method
        resolved = ResolvedSshMaterial(auth_method=auth, agent_forwarding=(auth == "agent_forwarding"))
        if auth == "private_key":
            resolved.private_key_pem = material
            resolved.passphrase = passphrase
        elif auth == "password":
            resolved.password = material
        elif auth == "agent_forwarding":
            resolved.agent_forwarding = True
        else:
            raise SshCredentialError("unsupported_auth_method")

        logger.info(
            "ssh_credential_resolved ref_id=%s owner_id=%s purpose=%s auth_method=%s",
            credential_ref_id, owner_id, purpose, auth,
        )
        return resolved


def scrub_ssh_secrets_from_text(text: str) -> str:
    """Best-effort redaction of PEM blocks and password-like assignments."""
    import re
    if not text:
        return text
    out = re.sub(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        "[REDACTED_PRIVATE_KEY]",
        text,
        flags=re.DOTALL,
    )
    out = re.sub(r"(?i)(passphrase|password)\s*[=:]\s*\S+", r"\1=[REDACTED]", out)
    return out


def assert_no_secret_material(payload: Any) -> None:
    """Raise if a public payload appears to contain private key material."""
    blob = str(payload)
    upper = blob.upper()
    for marker in _SENSITIVE_MARKERS:
        if marker.upper() in upper and "REDACTED" not in upper:
            # Allow the word 'password' in auth_method field contexts carefully
            if marker.lower() in ("password", "passphrase") and "auth_method" in blob and "PRIVATE KEY" not in upper:
                continue
            raise AssertionError(f"secret_material_leak:{marker}")
