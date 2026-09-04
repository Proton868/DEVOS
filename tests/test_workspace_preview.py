"""Workspace preview — isolation, scoped credentials, readiness, CSP."""
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
    fs.write(".env", "OPENROUTER_API_KEY=secret-value")
    fs.write("id_rsa", "PRIVATE KEY MATERIAL")
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
async def test_preview_endpoint_security_matrix():
    await init_db()
    async with AsyncSessionLocal() as db:
        for uid, name, email in (
            (USER, "previewuser", "preview@test"),
            (OTHER, "otheruser", "other@test"),
        ):
            r = await db.execute(select(User).where(User.id == uid))
            if not r.scalar_one_or_none():
                db.add(User(id=uid, username=name, email=email, hashed_password="x"))
        await db.commit()

    session = make_jwt(USER)
    other_session = make_jwt(OTHER)
    preview = make_preview_token(USER, WS, ttl_seconds=300)
    wrong_ws = make_preview_token(USER, "not-this-ws", ttl_seconds=300)
    other_preview = make_preview_token(OTHER, OTHER_WS, ttl_seconds=300)

    from app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # unauthenticated
        r = await client.get(f"/api/files/{WS}/preview/index.html")
        assert r.status_code in (401, 403)

        # session auth OK
        r = await client.get(
            f"/api/files/{WS}/preview/index.html",
            headers={"Authorization": f"Bearer {session}"},
        )
        assert r.status_code == 200
        assert "TestSite" in r.text
        assert "Content-Security-Policy" in r.headers
        csp = r.headers["Content-Security-Policy"]
        assert "object-src 'none'" in csp
        assert "connect-src 'none'" in csp
        assert "script-src 'self'" in csp
        assert "nosniff" in r.headers.get("X-Content-Type-Options", "")

        # scoped preview token OK
        r = await client.get(f"/api/files/{WS}/preview/index.html?token={preview['token']}")
        assert r.status_code == 200
        assert r.headers.get("X-DevOS-Preview-Auth") == "preview_token"

        # preview token wrong workspace
        r = await client.get(f"/api/files/{WS}/preview/index.html?token={wrong_ws['token']}")
        assert r.status_code == 403

        # other user's preview token on our workspace
        r = await client.get(f"/api/files/{WS}/preview/index.html?token={other_preview['token']}")
        assert r.status_code == 403

        # secrets blocked
        for secret in (".env", "id_rsa"):
            r = await client.get(
                f"/api/files/{WS}/preview/{secret}",
                headers={"Authorization": f"Bearer {session}"},
            )
            assert r.status_code == 403, secret

        # missing
        r = await client.get(
            f"/api/files/{WS}/preview/nope.html",
            headers={"Authorization": f"Bearer {session}"},
        )
        assert r.status_code == 404

        # mint session
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
        # minted token works
        r = await client.get(data["preview_url"])
        assert r.status_code == 200

        # readiness endpoint
        r = await client.get(
            f"/api/files/{WS}/preview-readiness?path=index.html",
            headers={"Authorization": f"Bearer {session}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["path"] == "index.html"
        assert body["readiness"] in ("READY", "PENDING", "INVALID", "UNSUPPORTED")

        # readiness for secret path
        r = await client.get(
            f"/api/files/{WS}/preview-readiness?path=.env",
            headers={"Authorization": f"Bearer {session}"},
        )
        assert r.status_code == 200
        assert r.json()["readiness"] == "UNSUPPORTED"

        # other user cannot use our session on their path via our token project mismatch already tested
        r = await client.get(
            f"/api/files/{OTHER_WS}/preview/index.html",
            headers={"Authorization": f"Bearer {other_session}"},
        )
        assert r.status_code == 200
        assert "other" in r.text


def test_surface_intent_preview():
    from brain.personas import surface_intent_for_message
    si = surface_intent_for_message("Show me the result")
    assert si["surface"] == "preview"
