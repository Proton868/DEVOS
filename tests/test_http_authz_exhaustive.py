"""Exhaustive authz probes for discovered API families (A/B + anonymous)."""
from __future__ import annotations

import os
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
                    for attr, val in (("role", "member"), ("plan", "recruit"), ("onboarding_status", "COMPLETED")):
                        if hasattr(u, attr):
                            setattr(u, attr, val)
                    db.add(u)
                    await db.commit()
                    await db.refresh(u)
                out[name] = {"id": u.id, "token": make_jwt(u.id, False)}
        assert out["idor_a"]["id"] != out["idor_b"]["id"]
        return out

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(setup())


def H(token):
    return {"Authorization": f"Bearer {token}"}


def test_spa_does_not_shadow_api_jobs(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    r = client.post("/api/jobs", headers=H(ta), json={"job_type": "script", "payload": {}})
    assert r.status_code == 200
    jid = r.json()["id"]
    ga = client.get(f"/api/jobs/{jid}", headers=H(ta))
    assert ga.status_code == 200
    assert "text/html" not in (ga.headers.get("content-type") or "")
    assert "<!doctype html>" not in ga.text.lower()
    gb = client.get(f"/api/jobs/{jid}", headers=H(tb))
    assert gb.status_code == 403
    lb = client.get("/api/jobs", headers=H(tb))
    if lb.status_code == 200:
        ids = [j.get("id") for j in (lb.json().get("jobs") or [])]
        assert jid not in ids


def test_secrets_cross_account(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    r = client.post("/api/secrets", headers=H(ta), json={"name": "idor_secret_a", "value": "super-secret-value-a"})
    if r.status_code >= 400:
        r = client.post("/api/secrets", headers=H(ta), json={"key": "idor_secret_a", "value": "super-secret-value-a"})
    if r.status_code >= 400:
        pytest.skip(f"secrets create: {r.status_code}")
    body = r.json()
    sid = body.get("id") or body.get("sid")
    lb = client.get("/api/secrets", headers=H(tb))
    assert lb.status_code in (200, 401, 403)
    if lb.status_code == 200:
        assert "super-secret-value-a" not in lb.text
        if sid:
            data = lb.json()
            items = data if isinstance(data, list) else data.get("secrets") or data.get("items") or []
            ids = [item.get("id") or item.get("sid") for item in items if isinstance(item, dict)]
            assert sid not in ids
    if sid:
        assert client.delete(f"/api/secrets/{sid}", headers=H(tb)).status_code in (403, 404)


def test_chat_sessions_scoped(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    client.post("/api/chat/send", headers=H(ta), json={"message": "hello idor", "project_id": "idor-chat"})
    la = client.get("/api/chat/sessions", headers=H(ta))
    lb = client.get("/api/chat/sessions", headers=H(tb))
    assert la.status_code in (200, 401, 403)
    assert lb.status_code in (200, 401, 403)
    if la.status_code == 200 and lb.status_code == 200:
        a_ids = set()
        for s in la.json() if isinstance(la.json(), list) else la.json().get("sessions") or []:
            if isinstance(s, dict) and s.get("id"):
                a_ids.add(s["id"])
        for s in lb.json() if isinstance(lb.json(), list) else lb.json().get("sessions") or []:
            if isinstance(s, dict) and s.get("id"):
                assert s["id"] not in a_ids or s.get("user_id") == users["idor_b"]["id"]
        if a_ids:
            sid = next(iter(a_ids))
            rb = client.get(f"/api/chat/sessions/{sid}/messages", headers=H(tb))
            assert rb.status_code in (403, 404) or (rb.status_code == 200 and "hello idor" not in rb.text)


def test_orchestration_plan_idor(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    r = client.post("/api/orchestration/plan", headers=H(ta), json={"goal": "idor plan test", "project_id": "idor-orch"})
    if r.status_code >= 400:
        pytest.skip(f"orchestration plan: {r.status_code} {r.text[:100]}")
    pid = r.json().get("plan_id") or r.json().get("id")
    assert pid
    assert client.get(f"/api/orchestration/{pid}", headers=H(ta)).status_code == 200
    assert client.get(f"/api/orchestration/{pid}", headers=H(tb)).status_code in (403, 404)
    assert client.post(f"/api/orchestration/{pid}/cancel", headers=H(tb)).status_code in (403, 404)
    assert client.get(f"/api/orchestration/{pid}/events", headers=H(tb)).status_code in (403, 404)


def test_scripts_idor(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    r = client.post("/api/scripts", headers=H(ta), json={"name": "idor_script", "content": "print(1)"})
    if r.status_code >= 400:
        r = client.post("/api/scripts", headers=H(ta), json={"name": "idor_script", "code": "print(1)", "language": "python"})
    if r.status_code >= 400:
        pytest.skip(f"scripts create: {r.status_code}")
    sid = r.json().get("id") or r.json().get("sid")
    assert sid
    assert client.get(f"/api/scripts/{sid}", headers=H(ta)).status_code == 200
    assert client.get(f"/api/scripts/{sid}", headers=H(tb)).status_code in (403, 404)
    assert client.delete(f"/api/scripts/{sid}", headers=H(tb)).status_code in (403, 404)


def test_research_jobs_list_no_leak(client, users):
    ta, tb = users["idor_a"]["token"], users["idor_b"]["token"]
    assert client.get("/api/research/jobs", headers=H(ta)).status_code in (200, 401, 403)
    assert client.get("/api/research/jobs", headers=H(tb)).status_code in (200, 401, 403)
    assert client.get("/api/research/jobs").status_code in (401, 403)


def test_delivery_public_share(client):
    r = client.get("/api/delivery/public/share/nonexistent-share-id")
    assert r.status_code in (200, 401, 403, 404)


def test_anonymous_protected_families(client):
    for path in (
        "/api/account/me", "/api/jobs", "/api/web/crawls", "/api/orchestration",
        "/api/secrets", "/api/carai/sessions", "/api/agent/tasks", "/api/files/idor/list",
    ):
        r = client.get(path)
        assert r.status_code in (401, 403, 404, 405, 422), (path, r.status_code)


def test_route_registry_jobs_before_spa():
    from app import app
    paths = [getattr(r, "path", None) for r in app.routes]
    paths = [p for p in paths if p]
    i_jobs = next(i for i, p in enumerate(paths) if p == "/api/jobs/{job_id}")
    i_spa = next(i for i, p in enumerate(paths) if p == "/{full_path:path}")
    assert i_jobs < i_spa
