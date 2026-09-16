#!/usr/bin/env bash
set -euo pipefail
OPS="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "$OPS/lib/common.sh"
ROOT="$(repo_root)"
cd "$ROOT"

DEVOS_USER="${DEVOS_USER:-${SUDO_USER:-$(id -un)}}"
DEVOS_GROUP="${DEVOS_GROUP:-$DEVOS_USER}"
DEVOS_HOST="${DEVOS_HOST:-0.0.0.0}"
DEVOS_PORT="${DEVOS_PORT:-8000}"
UNIT_SRC="$OPS/templates/devos.service.in"
UNIT_DST="${DEVOS_SYSTEMD_PATH:-/etc/systemd/system/devos.service}"

[[ -f "$UNIT_SRC" ]] || die "missing $UNIT_SRC"
[[ -x "$ROOT/.venv/bin/uvicorn" ]] || die "uvicorn not installed in .venv"

UNIT_BODY="$(
  sed \
    -e "s|{{DEVOS_USER}}|$DEVOS_USER|g" \
    -e "s|{{DEVOS_GROUP}}|$DEVOS_GROUP|g" \
    -e "s|{{DEVOS_ROOT}}|$ROOT|g" \
    -e "s|{{DEVOS_HOST}}|$DEVOS_HOST|g" \
    -e "s|{{DEVOS_PORT}}|$DEVOS_PORT|g" \
    "$UNIT_SRC"
)"

mkdir -p "$ROOT/deploy"
printf '%s\n' "$UNIT_BODY" > "$ROOT/deploy/devos.service"

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  printf '%s\n' "$UNIT_BODY" > "$UNIT_DST"
  systemctl daemon-reload
  systemctl enable devos.service
  ok "Installed $UNIT_DST"
elif have_cmd sudo; then
  printf '%s\n' "$UNIT_BODY" | sudo tee "$UNIT_DST" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable devos.service
  ok "Installed $UNIT_DST via sudo"
else
  warn "No root — wrote deploy/devos.service only"
  echo "  sudo cp $ROOT/deploy/devos.service /etc/systemd/system/devos.service"
  echo "  sudo systemctl daemon-reload && sudo systemctl enable --now devos"
fi
