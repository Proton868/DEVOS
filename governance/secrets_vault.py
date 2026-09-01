"""
Governance — Secrets Vault.

Encrypts Flow script credentials at rest. Derives its Fernet key from
JWT_SECRET via a proper KDF rather than requiring yet another secret to be
configured and pinned — JWT_SECRET is already required to be set (Session 6
found the unpinned-default problem), so this reuses that requirement
instead of adding a second one a real deployment could just as easily
forget to set.

This is genuinely different from JWT signing: HMAC-signing a token and
Fernet-encrypting a credential are different cryptographic operations with
different key requirements, so the JWT_SECRET string is passed through
PBKDF2 first, not used directly as a Fernet key.

Per-secret salt (security-audit fix): NIST SP 800-132 recommends a unique
salt per derived key rather than one static salt shared across every secret
in the vault — a static salt means every secret's Fernet key is identical,
so a single leaked key decrypts the entire vault at once. Each new secret
now gets its own random 16-byte salt, generated at encrypt() time and
stored alongside the ciphertext (salts aren't secret; only JWT_SECRET is),
so a compromise of one derived key no longer implies compromise of all of
them.

Format: "<version>$<base64 salt>$<fernet token>". Legacy ciphertexts
(encrypted before this fix, or before the CaraiOS -> DevOS rename) don't
have this prefix — decrypt() falls back to the old static salts so
existing installations don't lose access to already-stored secrets. New
writes always use the new per-secret-salt format.
"""
import base64
import hashlib
import os
from cryptography.fernet import Fernet, InvalidToken

from core.config import settings

# Legacy static salts, tried in order for ciphertexts written before the
# per-secret-salt migration. "v1" predates the DevOS rebrand; "v2" covers
# the brief window (if any) where a deployment renamed the salt string
# without yet picking up the per-secret-salt fix.
_LEGACY_SALTS = (b"caraios-secrets-vault-v1", b"devos-secrets-vault-v1")

_FORMAT_VERSION = "v3"


def _derive_key(salt: bytes) -> bytes:
    key = hashlib.pbkdf2_hmac("sha256", settings.JWT_SECRET.encode(), salt, 100_000)
    return base64.urlsafe_b64encode(key)


async def get_user_secrets_dict(db, user_id: str) -> dict[str, str]:
    """Fetches and decrypts all of a user's stored secrets, keyed by name,
    for injection into a script's environment at execution time (see
    api/routes/scripts.py's run_script and governance/sandbox.py's
    _build_safe_env, which prefixes each key as SECRET_<NAME>). A secret
    that fails to decrypt (see decrypt()'s ValueError above) is skipped
    with a logged warning rather than crashing the whole script run over
    one bad credential."""
    import logging
    from sqlalchemy import select
    from core.database import Secret
    logger = logging.getLogger("devos.secrets_vault")

    result = await db.execute(select(Secret).where(Secret.owner_id == user_id))
    secrets_dict = {}
    for s in result.scalars().all():
        try:
            secrets_dict[s.name] = decrypt(s.encrypted_value)
        except ValueError as e:
            logger.warning(f"[secrets_vault] skipping undecryptable secret '{s.name}': {e}")
    return secrets_dict


def encrypt(plaintext: str) -> str:
    """Encrypts with a fresh random salt every call, so each stored secret
    has its own derived Fernet key (see module docstring)."""
    salt = os.urandom(16)
    fernet = Fernet(_derive_key(salt))
    token = fernet.encrypt(plaintext.encode()).decode()
    return f"{_FORMAT_VERSION}${base64.urlsafe_b64encode(salt).decode()}${token}"


def decrypt(ciphertext: str) -> str:
    # New format: "<version>$<b64 salt>$<fernet token>" — decode the salt
    # that was stored alongside the ciphertext and derive that secret's
    # own key.
    parts = ciphertext.split("$", 2)
    if len(parts) == 3 and parts[0] == _FORMAT_VERSION:
        try:
            salt = base64.urlsafe_b64decode(parts[1].encode())
            fernet = Fernet(_derive_key(salt))
            return fernet.decrypt(parts[2].encode()).decode()
        except (InvalidToken, ValueError):
            raise ValueError(
                "Could not decrypt this secret — likely JWT_SECRET changed since it was "
                "stored. Re-create the secret with the current JWT_SECRET."
            )

    # Legacy format: single static salt shared by the whole vault. Try each
    # known legacy salt so secrets written before this migration (under
    # either the old CaraiOS name or an interim DevOS rename) still work.
    for legacy_salt in _LEGACY_SALTS:
        try:
            fernet = Fernet(_derive_key(legacy_salt))
            return fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            continue

    raise ValueError(
        "Could not decrypt this secret — likely JWT_SECRET changed since it was "
        "stored. Re-create the secret with the current JWT_SECRET."
    )
