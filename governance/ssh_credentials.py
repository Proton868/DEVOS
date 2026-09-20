"""Secure SSH credential resolution for DevOS.

Private keys, passphrases, and passwords:
  - live only in secrets.encrypted_value (Fernet via secrets_vault)
  - are resolved server-side only after owner + non-revoked checks
  - are never returned from public serializers or agent capability handles
  - must not appear in logs, SSE, error messages, artifacts, or LLM context

Agents receive capability handles (credential_ref_id), not material.

Threat model covered by this module:
  malicious agent, prompt injection, accidental logging, cross-user access,
  exception traces, command-output reflection, analytics payloads.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

logger = logging.getLogger("devos.ssh_credentials")

# Substrings that indicate secret material may have escaped a boundary.
_SENSITIVE_MARKERS = (
    "PRIVATE KEY",
    "BEGIN OPENSSH",
    "BEGIN RSA",
    "BEGIN EC",
    "BEGIN DSA",
    "BEGIN ENCRYPTED",
)


class SshCredentialError(Exception):
    """Safe error — message must never include secret material."""

    def __init__(self, code: str = "credential_error"):
        # Only stable machine codes — never append user/secret content.
        self.code = str(code)[:64]
        super().__init__(self.code)


class SshCredentialDenied(SshCredentialError):
    def __init__(self, code: str = "credential_not_found"):
        super().__init__(code)


class SshCredentialRevoked(SshCredentialError):
    def __init__(self, code: str = "credential_revoked"):
        super().__init__(code)


class SshCredentialPolicyDenied(SshCredentialError):
    def __init__(self, code: str = "auth_method_not_permitted"):
        super().__init__(code)


@dataclass
class SshCredentialHandle:
    """Public-safe handle for APIs (still not for LLM prompts by itself)."""

    credential_ref_id: str
    owner_id: str
    name: str
    auth_method: str
    revoked: bool
    public_metadata: dict
    workspace_id: Optional[str] = None

    def to_public_dict(self) -> dict:
        return {
            "credential_ref_id": self.credential_ref_id,
            "name": self.name,
            "auth_method": self.auth_method,
            "revoked": self.revoked,
            "public_metadata": dict(self.public_metadata or {}),
            "workspace_id": self.workspace_id,
            # Explicit: no secret_id, ciphertext, PEM, password, passphrase
        }


@dataclass
class AgentSshCapabilityHandle:
    """What agents / LLM tool results may receive — capability identity only."""

    capability: str = "ucip:ssh.credential.use"
    credential_ref_id: str = ""
    connection_id: Optional[str] = None
    auth_method: str = ""
    revoked: bool = False

    def to_agent_dict(self) -> dict:
        """Minimal dict safe for tool results and agent context."""
        return {
            "capability": self.capability,
            "credential_ref_id": self.credential_ref_id,
            "connection_id": self.connection_id,
            "auth_method": self.auth_method,
            "revoked": self.revoked,
            # NEVER: private_key, password, passphrase, secret_id, encrypted_value
        }

    def to_llm_context(self) -> str:
        """Opaque description for prompts — no material."""
        return (
            f"[ssh_credential_handle ref={self.credential_ref_id} "
            f"method={self.auth_method} revoked={self.revoked}]"
        )


@dataclass
class ResolvedSshMaterial:
    """In-memory material for transport only. Do not serialize to JSON responses."""

    auth_method: str
    private_key_pem: Optional[str] = None
    password: Optional[str] = None
    passphrase: Optional[str] = None
    agent_forwarding: bool = False
    _cleared: bool = field(default=False, repr=False)

    def clear(self) -> None:
        self.private_key_pem = None
        self.password = None
        self.passphrase = None
        self._cleared = True

    def __repr__(self) -> str:
        return (
            f"ResolvedSshMaterial(auth_method={self.auth_method!r}, "
            f"has_key={bool(self.private_key_pem)}, has_password={bool(self.password)}, "
            f"has_passphrase={bool(self.passphrase)}, agent_forwarding={self.agent_forwarding}, "
            f"cleared={self._cleared})"
        )

    __str__ = __repr__

    def __del__(self) -> None:
        try:
            self.clear()
        except Exception:
            pass


# In-process audit ring (also logged). Durable audit can attach later to EvidenceRecord.
_ACCESS_AUDIT: list[dict] = []


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _audit(event: str, **fields: Any) -> None:
    """Record credential access events without secret material."""
    safe = {k: v for k, v in fields.items() if k not in (
        "material", "private_key", "password", "passphrase", "secret", "value",
    )}
    entry = {
        "event": event,
        "at": _utcnow().isoformat(),
        **{k: (str(v)[:128] if v is not None else None) for k, v in safe.items()},
    }
    _ACCESS_AUDIT.append(entry)
    if len(_ACCESS_AUDIT) > 500:
        del _ACCESS_AUDIT[:250]
    logger.info(
        "ssh_cred_audit event=%s %s",
        event,
        " ".join(f"{k}={v}" for k, v in safe.items() if v is not None),
    )


def get_access_audit(limit: int = 50) -> list[dict]:
    return list(_ACCESS_AUDIT[-limit:])


def clear_access_audit_for_tests() -> None:
    _ACCESS_AUDIT.clear()


def credential_ref_to_public(row) -> dict:
    """Serialize credential ref for API — never includes secret values or ciphertext."""
    return {
        "id": row.id,
        "name": row.name,
        "auth_method": row.auth_method,
        "revoked": bool(row.revoked),
        "public_metadata": dict(row.public_metadata or {}),
        "workspace_id": getattr(row, "workspace_id", None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        # secret_id / passphrase_secret_id intentionally omitted
    }


def _policy_allows_auth_method(auth_method: str) -> bool:
    """Password auth permitted only when explicitly allowed by settings."""
    auth_method = (auth_method or "").strip().lower()
    if auth_method in ("private_key", "agent_forwarding"):
        return True
    if auth_method == "password":
        try:
            from core.config import settings
            return bool(getattr(settings, "SSH_ALLOW_PASSWORD_AUTH", False))
        except Exception:
            return False
    return False


async def create_ssh_credential_ref(
    *,
    owner_id: str,
    tenant_id: Optional[str],
    name: str,
    auth_method: str,
    secret_plaintext: str,
    passphrase_plaintext: Optional[str] = None,
    public_metadata: Optional[dict] = None,
    workspace_id: Optional[str] = None,
) -> dict:
    """Store material in secrets vault; return public handle only."""
    from core.database import AsyncSessionLocal, Secret, SshCredentialRef, gen_id
    from governance.secrets_vault import encrypt

    auth_method = (auth_method or "").strip().lower()
    if auth_method not in ("private_key", "password", "agent_forwarding"):
        raise SshCredentialError("unsupported_auth_method")
    if not _policy_allows_auth_method(auth_method):
        raise SshCredentialPolicyDenied("auth_method_not_permitted")

    if auth_method == "agent_forwarding":
        secret_plaintext = secret_plaintext or ""
    if auth_method in ("private_key", "password") and not (secret_plaintext or "").strip():
        raise SshCredentialError("credential_material_required")

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
        # Optional workspace binding if column exists on model
        if workspace_id and hasattr(ref, "workspace_id"):
            setattr(ref, "workspace_id", workspace_id)
        db.add(ref)
        await db.commit()
        await db.refresh(ref)
        _audit(
            "credential_created",
            owner_id=owner_id,
            credential_ref_id=ref.id,
            auth_method=auth_method,
            workspace_id=workspace_id,
        )
        return credential_ref_to_public(ref)


async def get_credential_handle(owner_id: str, credential_ref_id: str) -> SshCredentialHandle:
    from core.database import AsyncSessionLocal, SshCredentialRef

    async with AsyncSessionLocal() as db:
        row = await db.get(SshCredentialRef, credential_ref_id)
        if not row or row.owner_id != owner_id:
            _audit("credential_handle_denied", owner_id=owner_id, credential_ref_id=credential_ref_id)
            raise SshCredentialDenied("credential_not_found")
        _audit("credential_handle_read", owner_id=owner_id, credential_ref_id=credential_ref_id)
        return SshCredentialHandle(
            credential_ref_id=row.id,
            owner_id=row.owner_id,
            name=row.name,
            auth_method=row.auth_method,
            revoked=bool(row.revoked),
            public_metadata=dict(row.public_metadata or {}),
            workspace_id=getattr(row, "workspace_id", None),
        )


def agent_capability_handle(
    *,
    credential_ref_id: str,
    auth_method: str,
    revoked: bool = False,
    connection_id: Optional[str] = None,
) -> AgentSshCapabilityHandle:
    """Build the only credential-related object agents should see."""
    return AgentSshCapabilityHandle(
        credential_ref_id=credential_ref_id,
        auth_method=auth_method,
        revoked=revoked,
        connection_id=connection_id,
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
        _audit("credential_revoked", owner_id=owner_id, credential_ref_id=credential_ref_id)
        return credential_ref_to_public(row)


async def resolve_ssh_material(
    *,
    owner_id: str,
    credential_ref_id: str,
    purpose: str = "ssh_transport",
    workspace_id: Optional[str] = None,
) -> ResolvedSshMaterial:
    """Decrypt material for authorized server-side transport only.

    Caller must clear() the result after use (prefer resolved_material() CM).
    Never pass the result to LLM/agent context, SSE, or API responses.
    """
    from core.database import AsyncSessionLocal, SshCredentialRef, Secret
    from governance.secrets_vault import decrypt

    async with AsyncSessionLocal() as db:
        row = await db.get(SshCredentialRef, credential_ref_id)
        if not row or row.owner_id != owner_id:
            _audit(
                "credential_resolve_denied",
                owner_id=owner_id,
                credential_ref_id=credential_ref_id,
                purpose=purpose,
            )
            raise SshCredentialDenied("credential_not_found")
        if row.revoked:
            _audit(
                "credential_resolve_revoked",
                owner_id=owner_id,
                credential_ref_id=credential_ref_id,
                purpose=purpose,
            )
            raise SshCredentialRevoked("credential_revoked")

        # Workspace isolation when both sides declare a workspace
        row_ws = getattr(row, "workspace_id", None)
        if workspace_id and row_ws and row_ws != workspace_id:
            _audit(
                "credential_resolve_workspace_mismatch",
                owner_id=owner_id,
                credential_ref_id=credential_ref_id,
            )
            raise SshCredentialDenied("credential_workspace_mismatch")

        secret = await db.get(Secret, row.secret_id)
        if not secret or secret.owner_id != owner_id:
            raise SshCredentialDenied("secret_not_found")

        try:
            material = decrypt(secret.encrypted_value)
        except Exception:
            _audit("credential_decrypt_failed", credential_ref_id=credential_ref_id)
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
        if not _policy_allows_auth_method(auth):
            raise SshCredentialPolicyDenied("auth_method_not_permitted")

        resolved = ResolvedSshMaterial(
            auth_method=auth,
            agent_forwarding=(auth == "agent_forwarding"),
        )
        if auth == "private_key":
            resolved.private_key_pem = material
            resolved.passphrase = passphrase
        elif auth == "password":
            resolved.password = material
        elif auth == "agent_forwarding":
            resolved.agent_forwarding = True
        else:
            raise SshCredentialError("unsupported_auth_method")

        _audit(
            "credential_resolved",
            owner_id=owner_id,
            credential_ref_id=credential_ref_id,
            purpose=purpose,
            auth_method=auth,
        )
        return resolved


@contextmanager
def resolved_material_sync(material: ResolvedSshMaterial) -> Iterator[ResolvedSshMaterial]:
    """Ensure clear() even on exceptions."""
    try:
        yield material
    finally:
        material.clear()


async def resolve_and_use(
    *,
    owner_id: str,
    credential_ref_id: str,
    purpose: str = "ssh_transport",
    workspace_id: Optional[str] = None,
):
    """Async context manager: resolve → yield → clear."""

    class _CM:
        def __init__(self):
            self.material: Optional[ResolvedSshMaterial] = None

        async def __aenter__(self):
            self.material = await resolve_ssh_material(
                owner_id=owner_id,
                credential_ref_id=credential_ref_id,
                purpose=purpose,
                workspace_id=workspace_id,
            )
            return self.material

        async def __aexit__(self, *exc):
            if self.material is not None:
                self.material.clear()
            return False

    return _CM()


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
    # Redact full Authorization header value (scheme + token)
    out = re.sub(
        r"(?i)authorization\s*:\s*[^\n\r]+",
        "Authorization: [REDACTED]",
        out,
    )
    return out


def assert_no_secret_material(payload: Any) -> None:
    """Raise if a public payload appears to contain private key material."""
    blob = str(payload)
    upper = blob.upper()
    for marker in _SENSITIVE_MARKERS:
        if marker.upper() in upper and "REDACTED" not in upper:
            raise AssertionError(f"secret_material_leak:{marker}")


def safe_error_dict(exc: BaseException) -> dict:
    """Map exceptions to API/SSE-safe payloads without secret content."""
    if isinstance(exc, SshCredentialError):
        return {"error": exc.code, "error_type": type(exc).__name__}
    return {"error": "ssh_credential_error", "error_type": type(exc).__name__}
