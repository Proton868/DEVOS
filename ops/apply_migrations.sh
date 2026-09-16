#!/usr/bin/env bash
# Apply repository schema: Supabase SQL migrations then SQLAlchemy align helpers.
set -euo pipefail
OPS="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "$OPS/lib/common.sh"
ROOT="$(repo_root)"
cd "$ROOT"
load_dotenv
VENV_PY="$ROOT/.venv/bin/python"
[[ -x "$VENV_PY" ]] || die "venv missing — run ops/install.sh first"

info "Dialect check"
"$VENV_PY" - <<'PY'
import os, sys
url = (os.environ.get("DATABASE_URL") or "").strip().lower()
req = (os.environ.get("REQUIRE_POSTGRES") or "true").lower() in ("1", "true", "yes")
if not url:
    print("ERROR: DATABASE_URL unset", file=sys.stderr); sys.exit(1)
if req and url.startswith("sqlite"):
    print("ERROR: SQLite forbidden when REQUIRE_POSTGRES=true", file=sys.stderr); sys.exit(1)
if req and not url.startswith("postgres"):
    print("ERROR: Postgres/Supabase required", file=sys.stderr); sys.exit(1)
print("dialect check OK")
PY

info "supabase/migrations via scripts/apply_supabase_migrations.py"
PYTHONPATH="$ROOT" "$VENV_PY" "$ROOT/scripts/apply_supabase_migrations.py"

# SQLAlchemy create_all is NOT used for production Postgres (init_db no-ops).
# Optional: load durable capability seed data if present.
if [[ -f "$ROOT/scripts/migrate_agency_schema.py" ]]; then
  info "migrate_agency_schema (capability seed / verify)"
  # Allow seed load without create_all on postgres
  PYTHONPATH="$ROOT" "$VENV_PY" "$ROOT/scripts/migrate_agency_schema.py" || warn "migrate_agency_schema non-zero"
fi

ok "migrations step finished"
echo "NOTE: ops does not run destructive rollbacks. Restore from backup if a migration must be undone."
