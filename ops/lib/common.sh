#!/usr/bin/env bash
# Shared helpers for DevOS ops scripts (no secrets printed).
set -euo pipefail

ops_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  echo "$here"
}

repo_root() {
  local ops
  ops="$(ops_root)"
  echo "$(cd "$ops/.." && pwd)"
}

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }
ok() { echo "OK  $*"; }
warn() { echo "WARN $*" >&2; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

require_linux() {
  [[ "$(uname -s)" == "Linux" ]] || die "Linux required (Ubuntu 24.04 recommended). Found: $(uname -s)"
}

require_python() {
  have_cmd python3 || die "python3 is required"
  python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" \
    || die "Python 3.11+ required (found $(python3 --version 2>&1))"
}

load_dotenv() {
  local root envf
  root="$(repo_root)"
  envf="${DEVOS_ENV_FILE:-$root/.env}"
  if [[ -f "$envf" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$envf"
    set +a
  fi
}

mask_val() {
  local v="${1:-}"
  if [[ -z "$v" ]]; then echo "(empty)"; return; fi
  if [[ ${#v} -le 4 ]]; then echo "****"; return; fi
  echo "${v:0:2}****${v: -2}"
}
