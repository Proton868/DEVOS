"""Hegemon / Elder / Member world isolation and hierarchy (regression).

Does not call live production accounts. Uses deterministic local users with
server-side role fields — the same mapping production uses after Supabase sync.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-for-hegemon-elder-world-32charsxx")
os.environ.setdefault("DEVOS_JOB_WORKER", "0")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdmin!Passw0rd-NotDefault")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("AUTH_ENABLED", "true")
os.environ.setdefault("REQUIRE_POSTGRES", "false")

pytest.importorskip("fastapi")

from governance.platform_roles import (
    can_administer_platform,
    can_mutate_target_role,
    effective_platform_role,
    is_hegemon,
    is_elder_or_above,
    public_role_label,
)
from governance.identity_contract import reject_client_authority_fields


def test_hierarchy_member_elder_hegemon():
    m = SimpleNamespace(role="member", is_admin=False)
    e = SimpleNamespace(role="elder", is_admin=True)
    h = SimpleNamespace(role="hegemon", is_admin=True)
    assert effective_platform_role(m) == "member"
    assert effective_platform_role(e) == "elder"
    assert effective_platform_role(h) == "hegemon"
    assert not can_administer_platform(m)
    assert can_administer_platform(e)
    assert can_administer_platform(h)
    assert is_hegemon(h) and not is_hegemon(e)
    assert is_elder_or_above(e) and is_elder_or_above(h)
    assert not can_mutate_target_role(e, h)
    assert not can_mutate_target_role(e, m, "hegemon")
    assert can_mutate_target_role(h, e, "member")


def test_client_cannot_elevate_via_payload():
    cleaned = reject_client_authority_fields(
        {"role": "hegemon", "is_admin": True, "plan": "hegemon", "display_name": "x"}
    )
    assert "role" not in cleaned
    assert "is_admin" not in cleaned
    assert "plan" not in cleaned
    assert cleaned.get("display_name") == "x"


def test_auth_me_imports_platform_roles():
    src = open("api/routes/auth.py").read()
    assert "can_administer_platform" in src
    assert "from governance.platform_roles import" in src
    assert "public_role_label" in src


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def ranked_users():
    """Create Hegemon, Elder, Member with real JWTs."""
    from core.database import AsyncSessionLocal, User, init_db
    from api.routes.auth import make_jwt, hash_pw
    from sqlalchemy import select

    async def setup():
        await init_db()
        try:
            from core.account_schema import ensure_account_columns
            from core.database import engine
            await ensure_account_columns(engine)
        except Exception:
            pass
        out = {}
        specs = [
            ("world_hegemon", "hegemon", True, "hegemon"),
            ("world_elder", "elder", True, "elder"),
            ("world_member", "member", False, "recruit"),
        ]
        async with AsyncSessionLocal() as db:
            for name, role, admin, plan in specs:
                r = await db.execute(select(User).where(User.username == name))
                u = r.scalar_one_or_none()
                if not u:
                    u = User(
                        username=name,
                        email=f"{name}@test.local",
                        hashed_password=hash_pw("TestPass123!"),
                        is_admin=admin,
                        is_active=True,
                    )
                    db.add(u)
                u.role = role
                u.plan = plan
                u.is_admin = admin
                if hasattr(u, "onboarding_status"):
                    u.onboarding_status = "COMPLETED"
                await db.commit()
                await db.refresh(u)
                out[role] = {
                    "id": u.id,
                    "token": make_jwt(u.id, bool(admin)),
                    "username": name,
                }
        return out

    return asyncio.run(setup())


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_me_reflects_server_role(client, ranked_users):
    for role_key in ("hegemon", "elder", "member"):
        u = ranked_users[role_key]
        r = client.get("/api/auth/me", headers=_auth(u["token"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == u["id"]
        assert public_role_label(SimpleNamespace(role=body.get("role"), is_admin=body.get("is_admin"))) == role_key
        if role_key in ("hegemon", "elder"):
            assert body.get("is_admin") is True
        else:
            assert body.get("is_admin") is False


def test_profile_patch_cannot_self_elevate_to_hegemon(client, ranked_users):
    e = ranked_users["elder"]
    r = client.patch(
        "/api/account/profile",
        headers=_auth(e["token"]),
        json={"role": "hegemon", "plan": "hegemon", "is_admin": True, "display_name": "ElderX"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("role") == "elder"
    assert body.get("display_name") == "ElderX"


def test_member_denied_provider_admin(client, ranked_users):
    m = ranked_users["member"]
    # Provider settings mutation path requires Elder+
    r = client.put(
        "/api/models/settings",
        headers=_auth(m["token"]),
        json={"default_provider": "openrouter"},
    )
    # 403 or 404/405 depending on route shape — must not be 200 for member
    assert r.status_code in (403, 404, 405, 422), r.text
    if r.status_code == 200:
        pytest.fail("Member must not mutate provider settings")


def test_cross_world_me_ids_distinct(client, ranked_users):
    ids = {ranked_users[k]["id"] for k in ("hegemon", "elder", "member")}
    assert len(ids) == 3
    for role_key in ("hegemon", "elder", "member"):
        r = client.get("/api/auth/me", headers=_auth(ranked_users[role_key]["token"]))
        assert r.status_code == 200
        assert r.json()["id"] == ranked_users[role_key]["id"]


def test_bootstrap_owner_elder_cannot_steal_hegemon_label(client, ranked_users):
    """Elder calling bootstrap-owner must not become Hegemon unless configured owner username."""
    e = ranked_users["elder"]
    r = client.post("/api/account/bootstrap-owner", headers=_auth(e["token"]))
    # Endpoint returns public user; role must stay elder for non-owner
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("role") != "hegemon" or e["username"] == (os.environ.get("ADMIN_USERNAME") or "admin")
    # world_elder username is not admin → stay elder
    if e["username"] != "admin":
        assert body.get("role") == "elder"
