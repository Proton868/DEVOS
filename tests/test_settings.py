"""Tests for settings routes — user preferences and workspace layout persistence."""
import asyncio
import pytest
from fastapi.testclient import TestClient

# Ensure the new tables exist before any test runs
try:
    asyncio.run(__import__("core.database", fromlist=["init_db"]).init_db())
except RuntimeError:
    pass  # Already running in an event loop — tables should already exist


@pytest.fixture
def client_with_auth():
    """Create a test client with auth disabled."""
    from core.config import settings
    settings.AUTH_ENABLED = False
    from app import app
    with TestClient(app) as client:
        yield client
    settings.AUTH_ENABLED = True


class TestUserSettings:
    def test_get_empty_settings(self, client_with_auth):
        resp = client_with_auth.get("/api/settings")
        assert resp.status_code == 200
        assert resp.json()["settings"] == {}

    def test_put_and_get_settings(self, client_with_auth):
        resp = client_with_auth.put("/api/settings", json={"settings": {"theme": "dark", "fontSize": 14}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"]["theme"] == "dark"
        assert data["settings"]["fontSize"] == 14

        resp2 = client_with_auth.get("/api/settings")
        assert resp2.status_code == 200
        assert resp2.json()["settings"]["theme"] == "dark"

    def test_merge_settings(self, client_with_auth):
        client_with_auth.put("/api/settings", json={"settings": {"theme": "dark", "fontSize": 14}})
        resp = client_with_auth.put("/api/settings", json={"settings": {"density": "compact"}})
        assert resp.status_code == 200
        data = resp.json()["settings"]
        assert data["theme"] == "dark"
        assert data["fontSize"] == 14
        assert data["density"] == "compact"


class TestWorkspaceLayouts:
    def test_list_empty(self, client_with_auth):
        resp = client_with_auth.get("/api/settings/workspace-layouts")
        assert resp.status_code == 200
        assert resp.json()["layouts"] == []

    def test_create_and_list(self, client_with_auth):
        resp = client_with_auth.post("/api/settings/workspace-layouts", json={
            "name": "Workflow Builder",
            "layout_json": {"panels": {"left": "fileTree", "center": "workflow"}},
            "is_default": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Workflow Builder"
        assert data["is_default"] is True
        layout_id = data["id"]

        resp2 = client_with_auth.get("/api/settings/workspace-layouts")
        assert resp2.status_code == 200
        layouts = resp2.json()["layouts"]
        assert any(l["id"] == layout_id for l in layouts)

        resp3 = client_with_auth.get(f"/api/settings/workspace-layouts/{layout_id}")
        assert resp3.status_code == 200
        assert resp3.json()["name"] == "Workflow Builder"

    def test_create_multiple_layouts_default_handling(self, client_with_auth):
        r1 = client_with_auth.post("/api/settings/workspace-layouts", json={
            "name": "Layout A", "layout_json": {}, "is_default": True,
        })
        assert r1.json()["is_default"] is True

        r2 = client_with_auth.post("/api/settings/workspace-layouts", json={
            "name": "Layout B", "layout_json": {}, "is_default": True,
        })
        assert r2.json()["is_default"] is True

        r3 = client_with_auth.get(f"/api/settings/workspace-layouts/{r1.json()['id']}")
        assert r3.json()["is_default"] is False

    def test_delete_layout(self, client_with_auth):
        r = client_with_auth.post("/api/settings/workspace-layouts", json={
            "name": "To Delete", "layout_json": {},
        })
        layout_id = r.json()["id"]

        resp = client_with_auth.delete(f"/api/settings/workspace-layouts/{layout_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        resp2 = client_with_auth.get(f"/api/settings/workspace-layouts/{layout_id}")
        assert resp2.status_code == 404

    def test_delete_nonexistent(self, client_with_auth):
        resp = client_with_auth.delete("/api/settings/workspace-layouts/nonexistent-id")
        assert resp.status_code == 404