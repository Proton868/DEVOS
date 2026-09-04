"""Workspace artifact preview — isolation, MIME, secrets, auth."""
import asyncio
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.database import init_db
from execution.files import FileService, PathViolation


USER = "preview-test-user"
WS = "preview-ws"


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
    # secret-like — must not be served
    fs.write(".env", "OPENROUTER_API_KEY=secret-value")
    yield


def test_path_traversal_rejected():
    fs = FileService(USER, WS)
    with pytest.raises(PathViolation):
        fs._resolve("../etc/passwd")
    with pytest.raises(PathViolation):
        fs._resolve("/etc/passwd")


def test_valid_files_exist():
    fs = FileService(USER, WS)
    assert fs._resolve("index.html").is_file()
    assert fs._resolve("style.css").is_file()
    assert fs._resolve("nested/app.js").is_file()


@pytest.mark.asyncio
async def test_preview_endpoint_auth_and_content():
    await init_db()
    from app import app
    from api.routes.auth import make_jwt
    from core.database import User, AsyncSessionLocal
    from sqlalchemy import select

    # ensure user row for JWT sub
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.id == USER))
        u = r.scalar_one_or_none()
        if not u:
            db.add(User(id=USER, username="previewuser", email="preview@test", hashed_password="x"))
            await db.commit()

    token = make_jwt(USER)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # unauthenticated
        r = await client.get(f"/api/files/{WS}/preview/index.html")
        assert r.status_code in (401, 403)

        # valid html
        r = await client.get(
            f"/api/files/{WS}/preview/index.html",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "TestSite" in r.text
        assert "Hello" in r.text

        # css
        r = await client.get(
            f"/api/files/{WS}/preview/style.css",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert "css" in r.headers.get("content-type", "")
        assert "navy" in r.text

        # nested
        r = await client.get(
            f"/api/files/{WS}/preview/nested/app.js",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        # missing
        r = await client.get(
            f"/api/files/{WS}/preview/nope.html",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

        # secret blocked
        r = await client.get(
            f"/api/files/{WS}/preview/.env",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

        # traversal / escape attempts — never leak secrets or host files
        for evil in ("%2e%2e/%2e%2e/.env", "subdir/../../../.env"):
            r = await client.get(
                f"/api/files/{WS}/preview/{evil}",
                headers={"Authorization": f"Bearer {token}"},
            )
            body = r.text or ""
            assert "secret-value" not in body
            assert "OPENROUTER_API_KEY" not in body
            # If the request still hits the preview handler, it must not succeed
            if "text/html" in r.headers.get("content-type", "") and "TestSite" in body:
                raise AssertionError(f"unexpected workspace html for {evil}")
            if r.status_code == 200 and "navy" in body and "h1" in body:
                raise AssertionError(f"unexpected css leak for {evil}")

        # query token (iframe style)
        r = await client.get(f"/api/files/{WS}/preview/index.html?token={token}")
        assert r.status_code == 200
        assert "TestSite" in r.text


def test_surface_intent_preview():
    from brain.personas import surface_intent_for_message
    si = surface_intent_for_message("Show me the result")
    assert si["surface"] == "preview"
    assert si["action"] == "open"
    si2 = surface_intent_for_message("Build me a website")
    assert si2["surface"] == "ide"
