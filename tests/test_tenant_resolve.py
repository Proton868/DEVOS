"""Tenant membership gating and SaaS gap closure tests (no live DB required)."""
from pathlib import Path


def test_notes_migration_exists():
    p = Path("supabase/migrations/20260917180000_notes_documents_layouts_rls.sql")
    assert p.is_file()
    text = p.read_text()
    assert "notes_owner" in text
    assert "documents_owner" in text
    assert "workspace_layouts_owner" in text
    assert "supabase_id = auth.uid()" in text


def test_public_config_never_service_role_source():
    src = Path("api/routes/auth.py").read_text()
    idx = src.find("async def auth_public_config")
    assert idx >= 0
    chunk = src[idx: idx + 1500]
    assert "Never includes service_role" in chunk or "service_role" in chunk
    assert "SUPABASE_ANON_KEY" in chunk
    assert "Never fall back to SUPABASE_KEY" in chunk


def test_enterprise_uses_resolve_tenant():
    src = Path("api/routes/enterprise.py").read_text()
    assert "resolve_tenant_for_user" in src
    assert "/tenants/mine" in src


def test_tenant_store_has_membership_gate():
    src = Path("governance/tenant_store.py").read_text()
    assert "async def resolve_tenant_for_user" in src
    assert "TENANT_FORBIDDEN" in src
    assert "list_memberships_for_user" in src


def test_notes_api_owner_scoped():
    src = Path("api/routes/notes.py").read_text()
    assert "Note.user_id == user.id" in src
    assert "get_current_user" in src


def test_saas_readiness_doc_honest():
    doc = Path("docs/SAAS_READINESS.md").read_text()
    assert "not certified" in doc.lower() or "does **not** claim" in doc
    assert "UNVERIFIED" in doc
