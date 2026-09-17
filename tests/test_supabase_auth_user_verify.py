"""Auth-server fallback when local HS256 secret does not match token signature."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from api.routes import auth as auth_mod
from core.config import settings


@pytest.fixture(autouse=True)
def _restore():
    url, secret, anon, key = (
        settings.SUPABASE_URL,
        settings.SUPABASE_JWT_SECRET,
        settings.SUPABASE_ANON_KEY,
        settings.SUPABASE_KEY,
    )
    yield
    settings.SUPABASE_URL = url
    settings.SUPABASE_JWT_SECRET = secret
    settings.SUPABASE_ANON_KEY = anon
    settings.SUPABASE_KEY = key


def _hs_token(secret: str, sub: str = "uid-1"):
    now = int(time.time())
    return jwt.encode(
        {
            "sub": sub,
            "email": "u@example.com",
            "aud": "authenticated",
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
        },
        secret,
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_auth_user_fallback_when_hmac_secret_mismatches():
    settings.SUPABASE_URL = "https://heinpngifdqsykqzufhe.supabase.co"
    settings.SUPABASE_JWT_SECRET = "wrong-legacy-secret"
    settings.SUPABASE_ANON_KEY = "sb_publishable_test"
    token = _hs_token("actual-signing-secret-unknown-to-devos")

    # Local path must fail
    assert auth_mod.decode_supabase_token(token) is None

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "uid-1",
        "email": "u@example.com",
        "role": "authenticated",
        "app_metadata": {},
        "user_metadata": {},
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch.object(auth_mod.httpx, "AsyncClient", return_value=mock_client):
        payload = await auth_mod.verify_supabase_access_token(token)

    assert payload is not None
    assert payload["sub"] == "uid-1"
    assert payload["email"] == "u@example.com"
    assert payload.get("_verified_via") == "supabase_auth_user"
    mock_client.get.assert_awaited()
    args, kwargs = mock_client.get.await_args
    assert args[0].endswith("/auth/v1/user")
    assert kwargs["headers"]["Authorization"] == f"Bearer {token}"
    assert kwargs["headers"]["apikey"] == "sb_publishable_test"


@pytest.mark.asyncio
async def test_auth_user_fallback_rejects_unauthorized():
    settings.SUPABASE_URL = "https://heinpngifdqsykqzufhe.supabase.co"
    settings.SUPABASE_JWT_SECRET = "wrong"
    settings.SUPABASE_ANON_KEY = "sb_publishable_test"
    token = _hs_token("other")

    mock_resp = MagicMock()
    mock_resp.status_code = 401

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch.object(auth_mod.httpx, "AsyncClient", return_value=mock_client):
        payload = await auth_mod.verify_supabase_access_token(token)
    assert payload is None


@pytest.mark.asyncio
async def test_matching_hs256_secret_skips_auth_user_roundtrip():
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_JWT_SECRET = "correct-secret"
    settings.SUPABASE_ANON_KEY = "sb_publishable_test"
    token = _hs_token("correct-secret")

    with patch.object(auth_mod, "_verify_supabase_via_auth_user", new_callable=AsyncMock) as m:
        payload = await auth_mod.verify_supabase_access_token(token)
    assert payload is not None
    assert payload["sub"] == "uid-1"
    m.assert_not_awaited()
