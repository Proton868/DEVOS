"""Regression: Supabase session is authoritative over stale devos_token."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SB = (ROOT / "frontend-src" / "src" / "services" / "supabase.js").read_text()
API = (ROOT / "frontend-src" / "src" / "services" / "api.js").read_text()
APP = (ROOT / "frontend-src" / "src" / "App.jsx").read_text()


def test_no_stale_supabase_snapshot_export():
    assert "export const supabase = _client" not in SB
    assert "export const supabase =" not in SB
    assert "export function getSupabaseClient()" in SB
    assert "return _client" in SB


def test_get_token_supabase_first():
    # Must not return local token before checking session
    idx_fn = SB.index("export async function getToken()")
    body = SB[idx_fn : idx_fn + 900]
    assert "getSession()" in body
    assert "access_token" in body
    # localStorage fallback only after session check
    assert body.index("getSession()") < body.index("devos_token")


def test_sign_out_local_scope():
    assert 'signOut({ scope: "local" })' in SB or "signOut({ scope: 'local' })" in SB


def test_resolve_auth_token_supabase_first():
    idx = API.index("async function resolveAuthToken()")
    body = API[idx : idx + 700]
    assert "getSupabaseToken" in body or "getToken" in body
    # local getToken() must not win before supabase
    assert "const local = getToken();" not in body or body.index("getSupabaseToken") < body.index(
        "const local"
    )


def test_logout_uses_get_supabase_client_not_stale_export():
    idx = API.index("export async function logout")
    body = API[idx : idx + 1200]
    assert "getSupabaseClient" in body or "signOutSupabase" in body
    assert "const { supabase }" not in body
    assert "devos_token" in body  # still clears local token


def test_app_awaits_ensure_supabase_before_listener():
    assert "ensureSupabase" in APP
    assert "getSupabaseClient" in APP
    assert "onAuthStateChange" in APP
    # Must not use removed export
    assert "const { supabase }" not in APP
    idx = APP.index("onAuthStateChange")
    before = APP[max(0, idx - 400) : idx]
    assert "ensureSupabase" in before
    assert "getSupabaseClient" in before


def test_no_production_import_of_stale_supabase_export():
    src_root = ROOT / "frontend-src" / "src"
    offenders = []
    for path in src_root.rglob("*"):
        if path.suffix not in {".js", ".jsx", ".mjs", ".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "export const supabase" in text and path.name == "supabase.js":
            offenders.append(str(path))
        if "const { supabase }" in text or "{ supabase }" in text and "import" in text:
            # allow comments
            for i, line in enumerate(text.splitlines(), 1):
                if "const { supabase }" in line or (
                    "import" in line and "{ supabase }" in line
                ):
                    offenders.append(f"{path}:{i}:{line.strip()}")
    assert not offenders, offenders


def test_no_secrets_in_frontend_supabase_module():
    # Module must not embed secrets — comment warnings are fine
    lines = [ln for ln in SB.splitlines() if not ln.strip().startswith("*") and not ln.strip().startswith("/*") and not ln.strip().startswith("//")]
    code = "\n".join(lines).lower()
    assert "eyj" not in code  # no JWT blobs
    assert "service_role_key" not in code or "never" in SB.lower()
