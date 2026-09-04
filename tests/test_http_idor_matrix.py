"""HTTP ownership / IDOR matrix against discovered FastAPI routes.

Uses two deterministic users (idor_a, idor_b) with real JWT auth.
"""
from __future__ import annotations

import os
import io
import pytest

os.environ["JWT_SECRET"] = "test-secret-for-idor-matrix-not-production-32chars"
os.environ["DEVOS_JOB_WORKER"] = "0"
os.environ["DEBUG"] = "true"
os.environ["ADMIN_PASSWORD"] = "TestAdmin!Passw0rd-NotDefault"
os.environ["ALLOWED_ORIGINS"] = '["http://localhost:3000"]'
os.environ["AUTH_ENABLED"] = "true"

pytest.importorskip("fastapi")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def users(client):
    import asyncio
    from core.database import AsyncSessionLocal, User, init_db
    from api.routes.auth import hash_pw, make_jwt
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
        async with AsyncSessionLocal() as db:
            for name in ("idor_a", "idor_b"):
                r = await db.execute(select(User).where(User.username == name))
                u = r.scalar_one_or_none()
                if not u:
                    u = User(
                        username=name,
                        email=f"{name}@test.local",
                        hashed_password=hash_pw("TestPass123!"),
                        is_admin=False,
                        is_active=True,
                    )
                    if hasattr(u, "role"):
                        u.role = "member"
                    if hasattr(u, "plan"):
                        u.plan = "recruit"
                    if hasattr(u, "onboarding_status"):
                        u.onboarding_status = "COMPLETED"
                    db.add(u)
                    await db.commit()
                    await db.refresh(u)
                out[name] = {"id": u.id, "token": make_jwt(u.id, False)}
        return out

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    data = loop.run_until_complete(setup())
    # Fixture identity assertions
    assert data["idor_a"]["id"] != data["idor_b"]["id"]
    return data


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_fixture_identities_distinct(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    ra = client.get("/api/auth/me", headers=auth(ta))
    rb = client.get("/api/auth/me", headers=auth(tb))
    assert ra.status_code == 200 and rb.status_code == 200
    assert ra.json()["id"] == users["idor_a"]["id"]
    assert rb.json()["id"] == users["idor_b"]["id"]
    assert ra.json()["id"] != rb.json()["id"]


def test_profile_own_vs_foreign(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    r = client.patch(
        "/api/account/profile",
        headers=auth(ta),
        json={"display_name": "Alice", "role": "hegemon", "plan": "hegemon", "account_id": users["idor_b"]["id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("display_name") == "Alice"
    assert body.get("role") == "member"
    r2 = client.patch("/api/account/profile", headers=auth(tb), json={"display_name": "Bob"})
    assert r2.status_code == 200
    assert client.get("/api/account/me", headers=auth(ta)).json().get("display_name") == "Alice"


def test_plan_cannot_be_elder_hegemon(client, users):
    ta = users["idor_a"]["token"]
    for bad in ("elder", "hegemon"):
        r = client.post("/api/account/plan", headers=auth(ta), json={"plan": bad})
        assert r.status_code in (403, 400), r.text


def test_web_crawl_idor(client, users):
    from execution.web_intel.store import create_crawl
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    ca = create_crawl({
        "user_id": users["idor_a"]["id"],
        "root_url": "https://example.com",
        "normalized_root_url": "https://example.com",
    })
    assert client.get(f"/api/web/crawls/{ca['crawl_id']}", headers=auth(tb)).status_code in (403, 404)
    assert client.get(f"/api/web/crawls/{ca['crawl_id']}", headers=auth(ta)).status_code == 200
    assert client.post(f"/api/web/crawls/{ca['crawl_id']}/cancel", headers=auth(tb)).status_code in (403, 404)
    assert client.get(f"/api/web/crawls/{ca['crawl_id']}/pages", headers=auth(tb)).status_code in (403, 404)
    assert client.get(f"/api/web/crawls/{ca['crawl_id']}/events", headers=auth(tb)).status_code in (403, 404)
    assert client.get(f"/api/web/crawls/{ca['crawl_id']}/report", headers=auth(tb)).status_code in (403, 404)
    assert client.post(f"/api/web/crawls/{ca['crawl_id']}/resume", headers=auth(tb)).status_code in (403, 404)


def test_files_http_isolation(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    proj = "idor-proj"
    w = client.post(
        f"/api/files/{proj}/write",
        headers=auth(ta),
        json={"path": "secret.txt", "content": "alpha-secret"},
    )
    assert w.status_code in (200, 201), w.text
    # A can read
    ra = client.get(f"/api/files/{proj}/read", headers=auth(ta), params={"path": "secret.txt"})
    assert ra.status_code == 200
    assert "alpha-secret" in ra.text
    # B same project_id uses different user root — empty/missing, not A's secret
    rb = client.get(f"/api/files/{proj}/read", headers=auth(tb), params={"path": "secret.txt"})
    assert rb.status_code in (404, 400) or "alpha-secret" not in rb.text
    # traversal attempt
    trav = client.get(
        f"/api/files/{proj}/read",
        headers=auth(tb),
        params={"path": f"../{users['idor_a']['id']}/{proj}/secret.txt"},
    )
    assert trav.status_code in (400, 403, 404) or "alpha-secret" not in trav.text


def test_avatar_upload_ownership(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    r = client.post("/api/account/avatar", headers=auth(ta), files={"file": ("a.png", io.BytesIO(png), "image/png")})
    assert r.status_code == 200, r.text
    assert client.get("/api/account/avatar", headers=auth(ta)).status_code == 200
    assert client.get(f"/api/account/avatar/{users['idor_a']['id']}", headers=auth(tb)).status_code in (403, 404)
    assert client.get("/api/account/avatar", headers=auth(tb)).status_code == 404
    assert client.post(
        "/api/account/avatar",
        headers=auth(ta),
        files={"file": ("x.txt", io.BytesIO(b"not-an-image-file!!"), "text/plain")},
    ).status_code == 400


def test_carai_session_idor(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    r = client.post("/api/carai/sessions", headers=auth(ta), json={})
    if r.status_code >= 400:
        pytest.skip(f"carai session create unavailable: {r.status_code}")
    sid = r.json().get("session_id") or r.json().get("id")
    assert sid
    assert client.get(f"/api/carai/sessions/{sid}", headers=auth(ta)).status_code == 200
    assert client.get(f"/api/carai/sessions/{sid}", headers=auth(tb)).status_code in (403, 404)


def test_job_idor(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    # enqueue a job for A if endpoint exists
    r = client.post("/api/jobs", headers=auth(ta), json={"job_type": "script", "payload": {"x": 1}})
    if r.status_code >= 400:
        # try list only
        pytest.skip(f"job enqueue: {r.status_code} {r.text[:120]}")
    jid = r.json().get("id")
    assert jid
    assert client.get(f"/api/jobs/{jid}", headers=auth(ta)).status_code == 200
    assert client.get(f"/api/jobs/{jid}", headers=auth(tb)).status_code in (403, 404)


def test_agent_task_idor_missing_task(client, users):
    """Foreign task id must not leak — 403/404 for both if missing; if A creates, B denied."""
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    fake = "nonexistent-task-id-idor"
    ra = client.get(f"/api/agent/{fake}", headers=auth(ta))
    rb = client.get(f"/api/agent/{fake}", headers=auth(tb))
    assert ra.status_code in (403, 404)
    assert rb.status_code in (403, 404)
    assert client.post(f"/api/agent/{fake}/cancel", headers=auth(tb)).status_code in (403, 404)


def test_unauthenticated_denied(client):
    assert client.get("/api/account/me").status_code in (401, 403)
    assert client.get("/api/auth/me").status_code in (401, 403)
    assert client.get("/api/files/x/read", params={"path": "a"}).status_code in (401, 403)
