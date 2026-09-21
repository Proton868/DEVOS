"""Workspace preview — isolation, scoped credentials, readiness, CSP."""
import os

# Stabilize auth env before settings/app imports (Python 3.12 / dual mode).
os.environ.setdefault("AUTH_MODE", "dual")
os.environ.setdefault("AUTH_ENABLED", "true")
os.environ.setdefault("JWT_SECRET", "test-preview-jwt-secret-not-for-production")

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from core.database import init_db, User, AsyncSessionLocal
from execution.files import FileService, PathViolation
from api.routes.auth import make_jwt, make_preview_token, decode_preview_token, PREVIEW_TOKEN_TYP
from sqlalchemy import select

USER = "preview-test-user"
WS = "preview-ws"
OTHER = "other-user"
OTHER_WS = "other-ws"


@pytest.fixture(scope="module", autouse=True)
def _seed_workspace():
    fs = FileService(USER, WS)
    fs.write(
        "index.html",
        "<!DOCTYPE html><html><head>"
        '<link rel="stylesheet" href="style.css">'
        "</head><body><h1>TestSite</h1><p>Hello</p></body></html>",
    )
    fs.write("style.css", "h1{color:navy}")
    fs.write("nested/app.js", "console.log(1)")

    # Seed intentionally secret-looking files directly for preview security tests.
    # FileService.write() correctly refuses these paths in normal operation.
    (fs.root / ".env").write_text(
        "OPENROUTER_API_KEY=secret-value",
        encoding="utf-8",
    )
    (fs.root / "id_rsa").write_text(
        "PRIVATE KEY MATERIAL",
        encoding="utf-8",
    )

    FileService(OTHER, OTHER_WS).write("index.html", "<html><body>other</body></html>")
    yield


def test_path_traversal_rejected():
    fs = FileService(USER, WS)
    with pytest.raises(PathViolation):
        fs._resolve("../etc/passwd")
    with pytest.raises(PathViolation):
        fs._resolve("/etc/passwd")


def test_preview_token_scoped():
    tok = make_preview_token(USER, WS, path_prefix="", ttl_seconds=120)
    payload = decode_preview_token(tok["token"])
    assert payload is not None
    assert payload["typ"] == PREVIEW_TOKEN_TYP
    assert payload["project_id"] == WS
    assert payload["sub"] == USER
    assert payload["scope"] == "preview:read"
    # session JWT is not a preview token
    session = make_jwt(USER)
    assert decode_preview_token(session) is None


@pytest.mark.asyncio
async def test_preview_endpoint_security_matrix(monkeypatch):
    """Session JWT can preview own workspace; preview tokens stay non-session."""
    from core.config import settings
    from core.database import get_db
    from api.routes.auth import decode_local_token
    from app import app

    monkeypatch.setattr(settings, "AUTH_MODE", "dual")
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    secret = (settings.JWT_SECRET or "").strip() or "test-preview-jwt-secret-not-for-production"
    monkeypatch.setattr(settings, "JWT_SECRET", secret)

    await init_db()
    async with AsyncSessionLocal() as db:
        for uid, name, email in (
            (USER, "previewuser", "preview@test"),
            (OTHER, "otheruser", "other@test"),
        ):
            r = await db.execute(select(User).where(User.id == uid))
            u = r.scalar_one_or_none()
            if not u:
                db.add(User(
                    id=uid, username=name, email=email,
                    hashed_password="x", is_active=True, is_admin=False,
                ))
            else:
                u.is_active = True
                u.username = name
                u.email = email
        await db.commit()
        r = await db.execute(select(User).where(User.id == USER))
        assert r.scalar_one_or_none() is not None

    session = make_jwt(USER)
    other_session = make_jwt(OTHER)
    assert decode_local_token(session) is not None, "session JWT must verify under dual mode"
    assert decode_local_token(session).get("sub") == USER
    assert decode_local_token(make_preview_token(USER, WS)["token"]) is None
    preview = make_preview_token(USER, WS, ttl_seconds=300)
    wrong_ws = make_preview_token(USER, "not-this-ws", ttl_seconds=300)
    other_preview = make_preview_token(OTHER, OTHER_WS, ttl_seconds=300)

    async def _override_db():
        async with AsyncSessionLocal() as session_db:
            yield session_db

    app.dependency_overrides[get_db] = _override_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(f"/api/files/{WS}/preview/index.html")
            assert r.status_code in (401, 403)

            r = await client.get(
                f"/api/files/{WS}/preview/index.html",
                headers={"Authorization": f"Bearer {session}"},
            )
            assert r.status_code == 200, (
                f"session JWT preview expected 200, got {r.status_code}: {r.text[:300]}"
            )
            assert "TestSite" in r.text
            assert "Content-Security-Policy" in r.headers
            csp = r.headers["Content-Security-Policy"]
            assert "object-src 'none'" in csp
            assert "connect-src 'none'" in csp
            assert "script-src 'self'" in csp
            assert "nosniff" in r.headers.get("X-Content-Type-Options", "")

            r = await client.get(f"/api/files/{WS}/preview/index.html?token={preview['token']}")
            assert r.status_code == 200
            assert r.headers.get("X-DevOS-Preview-Auth") == "preview_token"

            r = await client.get(f"/api/files/{WS}/preview/index.html?token={wrong_ws['token']}")
            assert r.status_code == 403

            r = await client.get(f"/api/files/{WS}/preview/index.html?token={other_preview['token']}")
            assert r.status_code == 403

            for secret_name in (".env", "id_rsa"):
                r = await client.get(
                    f"/api/files/{WS}/preview/{secret_name}",
                    headers={"Authorization": f"Bearer {session}"},
                )
                assert r.status_code == 403, secret_name

            r = await client.get(
                f"/api/files/{WS}/preview/nope.html",
                headers={"Authorization": f"Bearer {session}"},
            )
            assert r.status_code == 404

            r = await client.post(
                f"/api/files/{WS}/preview-session",
                headers={"Authorization": f"Bearer {session}"},
                json={"path": "index.html"},
            )
            assert r.status_code == 200
            data = r.json()
            assert data["token"]
            assert data["readiness"]["readiness"] in ("READY", "PENDING")
            assert "preview_url" in data
            r = await client.get(data["preview_url"])
            assert r.status_code == 200

            r = await client.get(
                f"/api/files/{WS}/preview-readiness?path=index.html",
                headers={"Authorization": f"Bearer {session}"},
            )
            assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)



def test_surface_intent_preview():
    from brain.personas import surface_intent_for_message
    for phrase in (
        "Show me the result",
        "show me the website",
        "show me the preview",
        "preview the result",
        "open the result",
    ):
        si = surface_intent_for_message(phrase)
        assert si["surface"] == "preview", phrase
    # Must not steal ordinary chat / IDE intents
    chat = surface_intent_for_message("What do you think about this approach?")
    assert chat["surface"] == "chat"
    ide = surface_intent_for_message("Build me a website with a shoe catalog")
    assert ide["surface"] in ("ide", "chat", "flow")  # creation -> ide preferred
