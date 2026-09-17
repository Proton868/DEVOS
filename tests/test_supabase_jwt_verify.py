"""Supabase access-token verification: HS256 (JWT secret) + JWKS (ES256/RS256)."""
from __future__ import annotations

import time
from unittest.mock import patch

import jwt
import pytest

from api.routes import auth as auth_mod
from core.config import settings


@pytest.fixture(autouse=True)
def _restore_supabase_settings():
    url = settings.SUPABASE_URL
    secret = settings.SUPABASE_JWT_SECRET
    key = settings.SUPABASE_KEY
    yield
    settings.SUPABASE_URL = url
    settings.SUPABASE_JWT_SECRET = secret
    settings.SUPABASE_KEY = key


def _hs256_token(secret: str, sub: str = "user-sub-1", aud: str = "authenticated", exp_delta: int = 3600):
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": "user@example.com",
        "role": "authenticated",
        "aud": aud,
        "iat": now,
        "exp": now + exp_delta,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def test_hs256_valid_when_supabase_jwt_secret_configured():
    settings.SUPABASE_URL = "https://heinpngifdqsykqzufhe.supabase.co"
    settings.SUPABASE_JWT_SECRET = "unit-test-supabase-jwt-secret-value"
    token = _hs256_token(settings.SUPABASE_JWT_SECRET)
    # Must not consult DevOS JWT_SECRET
    with patch.object(settings, "JWT_SECRET", "devos-secret-must-not-verify-supabase"):
        payload = auth_mod.decode_supabase_token(token)
    assert payload is not None
    assert payload["sub"] == "user-sub-1"
    assert payload.get("email") == "user@example.com"


def test_hs256_rejected_with_wrong_secret():
    settings.SUPABASE_URL = "https://heinpngifdqsykqzufhe.supabase.co"
    settings.SUPABASE_JWT_SECRET = "correct-secret"
    token = _hs256_token("wrong-secret")
    assert auth_mod.decode_supabase_token(token) is None


def test_hs256_rejected_when_secret_not_configured():
    settings.SUPABASE_URL = "https://heinpngifdqsykqzufhe.supabase.co"
    settings.SUPABASE_JWT_SECRET = ""
    token = _hs256_token("any-secret")
    assert auth_mod.decode_supabase_token(token) is None


def test_hs256_does_not_use_devos_jwt_secret():
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_JWT_SECRET = ""
    devos = "devos-only-secret-xxxxxxxxxxxxxxxx"
    settings.JWT_SECRET = devos
    token = _hs256_token(devos)
    assert auth_mod.decode_supabase_token(token) is None


def test_jwks_es256_path_still_invoked_for_asymmetric_alg():
    """When alg is not HS256, JWKS path is attempted (mocked success)."""
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_JWT_SECRET = ""
    # Minimal forged header alg=ES256 — body is irrelevant because JWKS is mocked
    import base64, json

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    header = b64(json.dumps({"alg": "ES256", "typ": "JWT", "kid": "test"}).encode())
    body = b64(json.dumps({"sub": "es-user", "aud": "authenticated", "exp": int(time.time()) + 60}).encode())
    token = f"{header}.{body}.sig"

    with patch.object(auth_mod, "_decode_supabase_jwks", return_value={"sub": "es-user", "aud": "authenticated"}):
        payload = auth_mod.decode_supabase_token(token)
    assert payload["sub"] == "es-user"


def test_no_url_returns_none():
    settings.SUPABASE_URL = ""
    settings.SUPABASE_JWT_SECRET = "secret"
    token = _hs256_token("secret")
    assert auth_mod.decode_supabase_token(token) is None
