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


class TestPathTraversal:
    def test_file_path_escape_rejected(self, two_clients):
        a = two_clients["alice_c"]
        # Ensure project exists via tree
        tree = a.get("/api/files/default/tree")
        # tree may 200 even if empty
        assert tree.status_code in (200, 404)
        for bad in ("../../etc/passwd", "../bob_iso/secret", "/etc/passwd", "..\\..\\windows\\system32"):
            r = a.get("/api/files/default/read", params={"path": bad})
            assert r.status_code in (400, 404), f"{bad} -> {r.status_code} {r.text[:120]}"


class TestPathTraversalExtended:
    def test_canonical_escape_variants(self, two_clients, tmp_path, monkeypatch):
        from execution.files import FileService, PathViolation, PROJECTS_DIR
        import execution.files as fm
        monkeypatch.setattr(fm, "PROJECTS_DIR", tmp_path)
        svc = FileService("alice", "proj")
        (svc.root / "ok.txt").write_text("hi")
        assert svc.read("ok.txt")["content"] == "hi"
        denials = [
            "../../etc/passwd",
            "../alice/../bob/x",
            "foo/../../../etc/passwd",
            "a\x00b",
            "..\\..\\etc\\passwd",
            "foo/./../../etc/passwd",
        ]
        for bad in denials:
            try:
                svc._resolve(bad)
                # only fail if resolved outside root
                p = (svc.root / bad.replace("\\", "/").lstrip("/")).resolve()
                assert str(svc.root) in str(p) or p == svc.root
            except PathViolation:
                pass
        # symlink out
        outside = tmp_path / "outside_secret"
        outside.write_text("leak")
        link = svc.root / "linkout"
        try:
            link.symlink_to(outside)
            try:
                svc._resolve("linkout")
                raise AssertionError("symlink escape should raise PathViolation")
            except PathViolation:
                pass
        except OSError:
            pass


class TestWorkflowPersistence:
    def test_survives_engine_restart(self, two_clients, tmp_path, monkeypatch):
        """Database is source of truth — new engine instance must still load."""
        a = two_clients["alice_c"]
        r = a.post("/api/workflows", json={
            "name": "Persist Me",
            "description": "durable",
            "steps": [{"id": "s1", "type": "notify", "name": "n"}],
            "start_step": "s1",
        })
        assert r.status_code in (200, 201), r.text
        wid = r.json()["workflow"]["workflow_id"]
        rev1 = r.json()["workflow"].get("revision", 1)

        # Mutate in-memory engine if any — should not matter
        from brain.workflow import get_workflow_engine
        eng = get_workflow_engine()
        eng.delete(wid)  # wipe cache

        r2 = a.get(f"/api/workflows/{wid}")
        assert r2.status_code == 200, r2.text
        assert r2.json()["workflow"]["name"] == "Persist Me"
        assert r2.json()["workflow"]["workflow_id"] == wid

        # Update increments revision
        r3 = a.patch(f"/api/workflows/{wid}", json={"description": "updated"})
        assert r3.status_code == 200
        rev2 = r3.json()["workflow"].get("revision", 0)
        assert int(rev2) >= int(rev1) + 1

    def test_import_strips_owner_and_execute_snapshots(self, two_clients):
        a, b = two_clients["alice_c"], two_clients["bob_c"]
        payload = {
            "name": "Imported",
            "owner_id": two_clients["bob"].id,
            "steps": [{"id": "s1", "type": "notify", "name": "n"}],
            "start_step": "s1",
        }
        import json
        r = a.post("/api/workflows/import", json={
            "format": "json",
            "content": json.dumps(payload),
        })
        assert r.status_code in (200, 201), r.text
        wf = r.json()["workflow"]
        assert wf.get("owner_id") == two_clients["alice"].id
        wid = wf["workflow_id"]

        # Bob cannot execute
        assert b.post(f"/api/workflows/{wid}/execute").status_code in (404, 403)
        # Alice can snapshot execute → durable job
        ex = a.post(f"/api/workflows/{wid}/execute", json={})
        assert ex.status_code == 200
        body = ex.json()
        assert body["workflow_id"] == wid
        assert "workflow_version" in body
        assert body.get("job_id")


class TestWorkflowExecutionSnapshots:
    def test_snapshot_survives_definition_update_and_delete(self, two_clients):
        a, b = two_clients["alice_c"], two_clients["bob_c"]
        r = a.post("/api/workflows", json={
            "name": "SnapFlow",
            "steps": [{"id": "s1", "type": "notify", "name": "n"}],
            "start_step": "s1",
        })
        assert r.status_code in (200, 201), r.text
        wid = r.json()["workflow"]["workflow_id"]
        v1 = r.json()["workflow"].get("revision", 1)

        ex = a.post(f"/api/workflows/{wid}/execute", json={})
        assert ex.status_code == 200, ex.text
        body = ex.json()
        assert body["workflow_id"] == wid
        assert int(body["workflow_version"]) == int(v1)
        job_id = body["job_id"]
        assert job_id

        # Edit → new version
        a.patch(f"/api/workflows/{wid}", json={"description": "v2"})
        # Job still reports original version
        j = a.get(f"/api/workflows/jobs/{job_id}")
        assert j.status_code == 200, j.text
        assert int(j.json()["workflow_version"]) == int(v1)
        assert j.json().get("has_snapshot") is True
        assert int(j.json().get("snapshot_version") or 0) == int(v1)

        # Bob cannot see job or execute
        assert b.get(f"/api/workflows/jobs/{job_id}").status_code in (404, 403)
        assert b.post(f"/api/workflows/{wid}/execute").status_code in (404, 403)

        # Idempotency
        key = "idem-snap-1"
        e1 = a.post(f"/api/workflows/{wid}/execute", json={"idempotency_key": key})
        e2 = a.post(f"/api/workflows/{wid}/execute", json={"idempotency_key": key})
        assert e1.status_code == 200 and e2.status_code == 200
        assert e1.json()["job_id"] == e2.json()["job_id"]

        # Delete workflow — job remains
        assert a.delete(f"/api/workflows/{wid}").status_code == 200
        j2 = a.get(f"/api/workflows/jobs/{job_id}")
        assert j2.status_code == 200
        assert j2.json()["workflow_id"] == wid
        assert int(j2.json()["workflow_version"]) == int(v1)
