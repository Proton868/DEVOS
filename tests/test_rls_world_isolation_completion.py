"""RLS world-isolation completion for post-20260917 tables."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "supabase" / "migrations" / "20261006220000_rls_world_isolation_completion.sql"


def test_rls_completion_migration_exists():
    assert MIG.is_file()


def test_automation_run_records_has_owner_all_and_check():
    body = MIG.read_text()
    assert "ALTER TABLE public.automation_run_records ENABLE ROW LEVEL SECURITY" in body
    assert "CREATE POLICY automation_run_records_owner" in body
    assert "FOR ALL" in body
    assert "WITH CHECK" in body
    assert "supabase_id = auth.uid()" in body


def test_child_tables_are_parent_scoped():
    body = MIG.read_text()
    assert "CREATE POLICY maintenance_actions_via_request" in body
    assert "CREATE POLICY saga_steps_via_saga" in body
    assert "CREATE POLICY web_crawl_pages_via_crawl" in body
    assert "CREATE POLICY web_crawl_events_via_crawl" in body
    assert "CREATE POLICY incidents_owner" in body
    assert "CREATE POLICY agentic_runtime_checkpoints_owner" in body
    assert "CREATE POLICY maintenance_requests_owner" in body


def test_no_broad_using_true_in_completion_migration():
    for line in MIG.read_text().splitlines():
        s = line.strip()
        if s.startswith("--"):
            continue
        compact = s.replace(" ", "").lower()
        assert "using(true)" not in compact, line


def test_idempotent_drop_policy_if_exists():
    body = MIG.read_text()
    assert "DROP POLICY IF EXISTS automation_run_records_owner" in body
    assert "to_regclass('public.automation_run_records')" in body


def test_forward_discovery_includes_rls_completion():
    from scripts.apply_supabase_migrations import list_forward_migrations

    names = [p.name for p in list_forward_migrations(ROOT / "supabase" / "migrations")]
    assert "20261006220000_rls_world_isolation_completion.sql" in names
    assert "20260918200000_agentic_runtime_checkpoints.down.sql" not in names
