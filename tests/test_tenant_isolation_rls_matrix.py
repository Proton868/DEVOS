from __future__ import annotations
import os, re
from pathlib import Path
os.environ.setdefault("REQUIRE_POSTGRES", "false")
ROOT = Path(__file__).resolve().parents[1]
ALIGN = ROOT / "supabase/migrations/20260917110000_orm_sql_column_alignment.sql"
MIG = ROOT / "supabase/migrations/20260917120000_tenant_isolation_rls_completion.sql"
def test_alignment_exists():
    assert ALIGN.is_file() and "owner_id" in ALIGN.read_text()
def test_rls_migration_exists():
    assert MIG.is_file()
def test_memories_uses_supabase_id():
    body = MIG.read_text()
    assert "supabase_id = auth.uid()" in body
    assert "DROP POLICY IF EXISTS memories_owner_select" in body
def test_secrets_policy():
    assert "secrets_owner" in MIG.read_text()
def test_workflow_coalesce():
    assert "COALESCE(owner_id, user_id)" in MIG.read_text()
def test_file_service(tmp_path, monkeypatch):
    from execution import files as files_mod
    from execution.files import FileService, PathViolation
    base = tmp_path / "projects"; base.mkdir()
    monkeypatch.setattr(files_mod, "PROJECTS_DIR", base)
    a = FileService("user-a", "p1"); (a.root / "s.txt").write_text("a")
    assert not (FileService("user-b", "p1").root / "s.txt").exists()
    import pytest
    with pytest.raises(PathViolation):
        FileService("../user-a", "p1")
