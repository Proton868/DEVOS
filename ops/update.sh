#!/usr/bin/env bash
# git → deps → migrations → systemd → restart → verify (fail-fast)
set -euo pipefail
OPS="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "$OPS/lib/common.sh"
ROOT="$(repo_root)"
cd "$ROOT"
MODE="${DEVOS_DEPLOY_MODE:-production}"
SKIP_GIT="${DEVOS_SKIP_GIT:-0}"

info "DevOS ops update"

if [[ "$SKIP_GIT" != "1" && -d "$ROOT/.git" ]]; then
  info "git fetch + ff-only merge"
  git fetch origin
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  git merge --ff-only "origin/$BRANCH" || die "ff-only merge failed"
fi

[[ -x "$ROOT/.venv/bin/python" ]] || die "missing .venv — run ops/install.sh"
REQ="requirements.txt"
[[ -f "$ROOT/requirements-lite.txt" ]] && REQ="requirements-lite.txt"
info "pip install -r $REQ"
"$ROOT/.venv/bin/pip" install -r "$ROOT/$REQ"

PROD_ARGS=()
[[ "$MODE" == "production" ]] && PROD_ARGS=(--production)
"$ROOT/.venv/bin/python" "$OPS/env_validate.py" --env-file "$ROOT/.env" --require-file "${PROD_ARGS[@]+"${PROD_ARGS[@]}"}" \
  || die "env validation failed"

bash "$OPS/apply_migrations.sh"

if [[ "${DEVOS_INSTALL_SYSTEMD:-0}" == "1" ]] || [[ -f /etc/systemd/system/devos.service ]]; then
  bash "$OPS/install_systemd.sh" || warn "systemd refresh failed"
fi

if have_cmd systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^devos.service'; then
  info "restart devos.service"
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    systemctl restart devos.service
  else
    sudo systemctl restart devos.service
  fi
  sleep 2
fi

VERIFY_ARGS=(--development)
[[ "$MODE" == "production" ]] && VERIFY_ARGS=(--production)
bash "$OPS/verify.sh" "${VERIFY_ARGS[@]}" || die "verify failed"
ok "update complete"
