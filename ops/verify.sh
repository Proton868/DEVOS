#!/usr/bin/env bash
set -euo pipefail
OPS="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "$OPS/lib/common.sh"
ROOT="$(repo_root)"
cd "$ROOT"

PRODUCTION=0
for a in "$@"; do
  [[ "$a" == "--production" ]] && PRODUCTION=1
done

FAIL=0
pass() { ok "$1"; }
fail() { echo "FAIL $1" >&2; FAIL=1; }

info "verify production=$PRODUCTION"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  pass "venv"
  "$ROOT/.venv/bin/python" -c "import sys; assert sys.version_info >= (3, 11)"
  pass "python>=3.11"
else
  fail "venv"
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  if PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -c "import app, core.config, core.database, core.sync_session"; then
    pass "imports"
  else
    fail "imports"
  fi
fi

if [[ -f "$ROOT/.env" ]]; then
  pass ".env present"
  ARGS=(--env-file "$ROOT/.env")
  [[ "$PRODUCTION" -eq 1 ]] && ARGS+=(--production)
  if "$ROOT/.venv/bin/python" "$OPS/env_validate.py" "${ARGS[@]}"; then
    pass "env_validate"
  else
    fail "env_validate"
  fi
else
  fail ".env missing"
fi

PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" - <<'PY' || FAIL=1
import sys
from core.config import settings
url = (settings.DATABASE_URL or "").lower()
req = bool(getattr(settings, "REQUIRE_POSTGRES", True))
if req and url.startswith("sqlite"):
    print("FAIL SQLite with REQUIRE_POSTGRES"); sys.exit(1)
print("OK architecture flags DEFAULT_PROVIDER=", getattr(settings, "DEFAULT_PROVIDER", None))
print("OK REQUIRE_POSTGRES=", req)
PY

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  set +e
  OUT=$(PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" - <<'PY' 2>&1
import asyncio, sys
async def main():
    from core.database import engine
    from sqlalchemy import text
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("OK db connectivity")
    except Exception as e:
        print("FAIL db:", type(e).__name__); sys.exit(1)
asyncio.run(main())
PY
)
  RC=$?
  set -e
  echo "$OUT" | sed -E 's#(postgres(ql)?(\+[a-z]+)?)://[^@]+@#\1://***:***@#g'
  [[ $RC -eq 0 ]] || fail "db connectivity"
fi

if have_cmd systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^devos.service'; then
  if systemctl is-active --quiet devos.service; then
    pass "systemd active"
  else
    fail "systemd inactive"
  fi
else
  warn "systemd unit not installed"
fi

PORT="${DEVOS_PORT:-8000}"
if have_cmd curl; then
  if curl -fsS -m 5 "http://127.0.0.1:${PORT}/api/health" -o /tmp/devos_health_verify.json; then
    pass "HTTP /api/health"
  else
    fail "HTTP /api/health"
  fi
  rm -f /tmp/devos_health_verify.json
else
  warn "curl missing"
fi

[[ "$FAIL" -eq 0 ]] || { echo "verify: FAILED"; exit 1; }
echo "verify: OK"
