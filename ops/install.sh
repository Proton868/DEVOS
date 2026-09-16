#!/usr/bin/env bash
# DevOS production-oriented bootstrap for Ubuntu 24.04+ (idempotent).
set -euo pipefail
OPS="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "$OPS/lib/common.sh"
ROOT="$(repo_root)"
cd "$ROOT"

MODE="${DEVOS_DEPLOY_MODE:-production}"
INSTALL_SYSTEMD="${DEVOS_INSTALL_SYSTEMD:-0}"

require_linux
require_python
info "DevOS ops install — root=$ROOT mode=$MODE"

if have_cmd apt-get; then
  info "System packages"
  PKGS="python3-venv python3-full python3-pip build-essential curl git ca-certificates"
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    apt-get update -qq
    apt-get install -y -qq $PKGS || warn "apt issues"
  elif have_cmd sudo; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq $PKGS || warn "apt issues"
  else
    warn "No root/sudo for apt — ensure python3-venv exists"
  fi
fi

info "Runtime directories"
mkdir -p "$ROOT/data" "$ROOT/data/checkpoints" "$ROOT/data/sandbox" \
  "$ROOT/data/projects" "$ROOT/data/evidence" "$ROOT/logs" "$ROOT/.tools"

VENV="$ROOT/.venv"
if [[ -x "$VENV/bin/python" ]]; then
  info "Reusing venv"
else
  info "Creating venv"
  python3 -m venv "$VENV" || die "venv failed — install python3-venv"
fi
"$VENV/bin/python" -m pip install --upgrade pip -q
REQ="requirements.txt"
[[ -f "$ROOT/requirements-lite.txt" ]] && REQ="requirements-lite.txt"
info "pip install -r $REQ"
"$VENV/bin/pip" install -r "$ROOT/$REQ"

if [[ ! -f "$ROOT/.env" ]]; then
  [[ -f "$ROOT/.env.example" ]] || die "missing .env.example"
  info "Creating .env from .env.example (not overwriting later)"
  cp "$ROOT/.env.example" "$ROOT/.env"
  GEN="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  "$VENV/bin/python" - <<PY
from pathlib import Path
p = Path("$ROOT/.env")
lines = []
found = False
for line in p.read_text().splitlines():
    if line.startswith("JWT_SECRET="):
        lines.append("JWT_SECRET=$GEN")
        found = True
    else:
        lines.append(line)
if not found:
    lines.append("JWT_SECRET=$GEN")
p.write_text("\n".join(lines) + "\n")
PY
  ok "Generated JWT_SECRET (value not printed)"
else
  info "Preserving existing .env"
fi

PROD_ARGS=()
[[ "$MODE" == "production" ]] && PROD_ARGS=(--production)
info "env_validate"
if ! "$VENV/bin/python" "$OPS/env_validate.py" --env-file "$ROOT/.env" "${PROD_ARGS[@]+"${PROD_ARGS[@]}"}"; then
  if [[ "$MODE" == "production" ]]; then
    die "env validation failed — fix .env"
  fi
  warn "env validation failed (development continues)"
fi

info "migrations"
if [[ "$MODE" == "production" ]]; then
  bash "$OPS/apply_migrations.sh"
else
  bash "$OPS/apply_migrations.sh" || warn "migrations failed"
fi

if [[ "$INSTALL_SYSTEMD" == "1" ]]; then
  bash "$OPS/install_systemd.sh"
fi

ok "ops/install.sh complete"
echo "Edit .env for DATABASE_URL/secrets, then: DEVOS_INSTALL_SYSTEMD=1 DEVOS_DEPLOY_MODE=production $OPS/install.sh"
echo "Verify: $OPS/verify.sh --production"
