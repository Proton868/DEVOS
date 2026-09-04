"""HTTP test helpers — real FastAPI app, isolated DB, two users."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Ensure JWT secret for tests before app import
os.environ.setdefault("JWT_SECRET", "test-secret-for-idor-matrix-not-production")
os.environ.setdefault("DEVOS_JOB_WORKER", "0")


@pytest.fixture(scope="module")
def app_client():
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as client:
        yield client


def _make_user(client, username: str, password: str = "TestPass123!"):
    """Register via local login admin path or direct DB — prefer login if admin exists."""
    # Try login first (admin may exist from startup)
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    if r.status_code == 200:
        data = r.json()
        token = data.get("token") or data.get("access_token")
        return data.get("user") or data, token
    # Create via internal helpers
    import asyncio
    from core.database import AsyncSessionLocal, User, init_db
    from api.routes.auth import hash_pw, make_jwt

    async def _create():
        await init_db()
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            r = await db.execute(select(User).where(User.username == username))
            u = r.scalar_one_or_none()
            if not u:
                u = User(
                    username=username,
                    email=f"{username}@test.local",
                    hashed_password=hash_pw(password),
                    is_admin=False,
                    is_active=True,
                )
                db.add(u)
                await db.commit()
                await db.refresh(u)
            token = make_jwt(u.id, u.is_admin)
            return {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "is_admin": u.is_admin,
            }, token

    return asyncio.get_event_loop().run_until_complete(_create())
