"""Cross-user isolation tests (AUTH_ENABLED=True, distinct JWTs)."""
from __future__ import annotations

import asyncio
import bcrypt
import pytest
from fastapi.testclient import TestClient


def _run(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


@pytest.fixture()
def two_clients(tmp_path, monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "JWT_SECRET", "isolation-test-jwt-secret-32b!!")
    # Isolate DB file
    db_url = f"sqlite+aiosqlite:///{tmp_path}/iso.db"
    monkeypatch.setattr(settings, "DATABASE_URL", db_url)

    # Rebuild engine against the temp DB
    import core.database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(db_url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)

    async def seed():
        from core.database import Base, User, init_db
        from governance.tenant_store import ensure_personal_tenant
        await init_db()
        async with dbmod.AsyncSessionLocal() as db:
            users = {}
            for uname, email in (("alice_iso", "alice_iso@test.local"), ("bob_iso", "bob_iso@test.local")):
                hashed = bcrypt.hashpw(b"Passw0rd!iso", bcrypt.gensalt()).decode()
                u = User(username=uname, email=email, hashed_password=hashed, is_admin=False)
                db.add(u)
                await db.commit()
                await db.refresh(u)
                await ensure_personal_tenant(db, u)
                users[uname] = u
            return users

    users = _run(seed())
    from api.routes.auth import make_jwt
    from app import app

    def as_user(u):
        token = make_jwt(u.id, False)
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {token}"})
        return c

    return {
        "alice": users["alice_iso"],
        "bob": users["bob_iso"],
        "alice_c": as_user(users["alice_iso"]),
        "bob_c": as_user(users["bob_iso"]),
    }


class TestSettingsIsolation:
    def test_settings_not_shared(self, two_clients):
        a, b = two_clients["alice_c"], two_clients["bob_c"]
        r = a.put("/api/settings", json={"settings": {"theme": "alice-only", "marker": "A"}})
        assert r.status_code == 200
        bob = b.get("/api/settings")
        assert bob.status_code == 200
        s = bob.json().get("settings") or {}
        assert s.get("marker") != "A"
        assert s.get("theme") != "alice-only"

    def test_forged_user_id_in_body_ignored(self, two_clients):
        b = two_clients["bob_c"]
        alice_id = two_clients["alice"].id
        r = b.put("/api/settings", json={
            "settings": {"pwned": True},
            "user_id": alice_id,
            "tenant_id": "forged",
        })
        assert r.status_code == 200
        alice_settings = two_clients["alice_c"].get("/api/settings").json().get("settings") or {}
        assert alice_settings.get("pwned") is not True


class TestSecretsIsolation:
    def test_secret_not_visible_cross_user_and_no_raw_value(self, two_clients):
        a, b = two_clients["alice_c"], two_clients["bob_c"]
        r = a.post("/api/secrets", json={
            "name": "ALICE_KEY",
            "value": "sk-should-never-appear",
            "description": "private",
        })
        assert r.status_code in (200, 201)
        body = r.json()
        assert "value" not in body and "encrypted_value" not in body
        sid = body["id"]

        bl = b.get("/api/secrets")
        assert bl.status_code == 200
        secrets = bl.json().get("secrets") or []
        assert all(s.get("id") != sid for s in secrets)
        assert all("value" not in s and "encrypted_value" not in s for s in secrets)

        assert b.delete(f"/api/secrets/{sid}").status_code in (404, 403)
        assert any(s["id"] == sid for s in (a.get("/api/secrets").json().get("secrets") or []))


class TestScriptsIsolation:
    def test_script_get_delete_blocked(self, two_clients):
        a, b = two_clients["alice_c"], two_clients["bob_c"]
        r = a.post("/api/scripts", json={
            "name": "alice-job",
            "code": "print(1)",
            "language": "python",
        })
        assert r.status_code in (200, 201), r.text
        sid = r.json()["id"]
        assert b.get(f"/api/scripts/{sid}").status_code in (404, 403)
        assert b.delete(f"/api/scripts/{sid}").status_code in (404, 403)
        assert a.get(f"/api/scripts/{sid}").status_code == 200


class TestChatIsolation:
    def test_session_messages_scoped(self, two_clients):
        a, b = two_clients["alice_c"], two_clients["bob_c"]
        # Create session indirectly: POST /api/chat/send creates session
        # Use a non-streaming friendly approach: list is empty for bob always
        # Seed a ChatSession row for alice via DB would be ideal; use API list after send if available
        from core.database import AsyncSessionLocal, ChatSession
        import core.database as dbmod

        async def create_session():
            async with dbmod.AsyncSessionLocal() as db:
                s = ChatSession(user_id=two_clients["alice"].id, title="Alice private chat")
                db.add(s)
                await db.commit()
                await db.refresh(s)
                return s.id

        sid = _run(create_session())
        # Bob cannot list it
        bob_sessions = b.get("/api/chat/sessions")
        assert bob_sessions.status_code == 200
        ids = [x["id"] for x in bob_sessions.json()]
        assert sid not in ids
        # Bob cannot read messages
        assert b.get(f"/api/chat/sessions/{sid}/messages").status_code in (404, 403)
        # Alice can
        assert a.get(f"/api/chat/sessions/{sid}/messages").status_code == 200
        assert a.delete(f"/api/chat/sessions/{sid}").status_code == 200


class TestAuthGate:
    def test_unauthenticated_rejected(self, two_clients, monkeypatch):
        from core.config import settings
        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        from app import app
        c = TestClient(app)
        assert c.get("/api/settings").status_code in (401, 403)
        assert c.get("/api/settings", headers={"Authorization": "Bearer garbage"}).status_code in (401, 403)


class TestWorkflowIsolation:
    def test_workflow_not_visible_cross_user(self, two_clients):
        a, b = two_clients["alice_c"], two_clients["bob_c"]
        r = a.post("/api/workflows", json={
            "name": "Alice Flow",
            "description": "private",
            "steps": [{"id": "s1", "type": "notify", "name": "n"}],
            "start_step": "s1",
        })
        assert r.status_code in (200, 201), r.text
        wid = r.json()["workflow"]["workflow_id"]
        assert b.get(f"/api/workflows/{wid}").status_code in (404, 403)
        bob_list = b.get("/api/workflows")
        assert bob_list.status_code == 200
        ids = [w.get("workflow_id") for w in bob_list.json().get("workflows", [])]
        assert wid not in ids
        assert b.delete(f"/api/workflows/{wid}").status_code in (404, 403)
        assert a.get(f"/api/workflows/{wid}").status_code == 200


class TestProviderCredentialIsolation:
    def test_credential_status_only_and_cross_user(self, two_clients):
        a, b = two_clients["alice_c"], two_clients["bob_c"]
        r = a.put("/api/models/providers/openrouter/credential", json={
            "provider": "openrouter",
            "api_key": "sk-alice-secret-key-value",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("credentials_configured") is True
        assert "api_key" not in body
        assert "sk-" not in str(body)

        # Bob status for same provider should be false (his own secret absent)
        st = b.get("/api/models/providers/openrouter/credential")
        assert st.status_code == 200
        assert st.json().get("credentials_configured") is False

        # Alice sees configured
        st_a = a.get("/api/models/providers/openrouter/credential")
        assert st_a.json().get("credentials_configured") is True

        # Secrets list never leaks value
        secrets = a.get("/api/secrets").json().get("secrets") or []
        for s in secrets:
            assert "value" not in s and "encrypted_value" not in s
            assert "sk-alice" not in str(s)

        # Bob cannot delete alice credential by guessing provider path
        # (delete only affects bob's own secret — which doesn't exist → 404)
        assert b.delete("/api/models/providers/openrouter/credential").status_code in (404, 403)
        # Alice still has it
        assert a.get("/api/models/providers/openrouter/credential").json()["credentials_configured"] is True


class TestModelPreferenceIsolation:
    def test_model_prefs_independent(self, two_clients):
        a, b = two_clients["alice_c"], two_clients["bob_c"]
        assert a.put("/api/settings/models", json={
            "settings": {"default_chat": "model-A", "default_coding": "code-A"}
        }).status_code == 200
        assert b.put("/api/settings/models", json={
            "settings": {"default_chat": "model-B", "default_coding": "code-B"}
        }).status_code == 200
        ma = a.get("/api/settings/models").json()["models"]
        mb = b.get("/api/settings/models").json()["models"]
        assert ma["default_chat"] == "model-A"
        assert mb["default_chat"] == "model-B"
        assert ma["default_chat"] != mb["default_chat"]
