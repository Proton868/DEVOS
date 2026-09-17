#!/usr/bin/env bash
# Enable strong/restricted isolation for untrusted project commands on Prime.
# Does NOT weaken policy. Does NOT install via agent. Operator-run only.
#
# Prefer (in order): Docker sandbox → bubblewrap → firejail
# unshare-only (network_only) is insufficient for untrusted code.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== DevOS strong isolation enablement (operator) ==="

have() { command -v "$1" >/dev/null 2>&1; }

if have docker && docker info >/dev/null 2>&1; then
  echo "[ok] docker available"
  if [[ -f .env ]]; then
    if grep -q '^DEVOS_USE_DOCKER_SANDBOX=' .env; then
      sed -i 's/^DEVOS_USE_DOCKER_SANDBOX=.*/DEVOS_USE_DOCKER_SANDBOX=1/' .env
    else
      echo 'DEVOS_USE_DOCKER_SANDBOX=1' >> .env
    fi
  else
    echo "WARN: no .env — export DEVOS_USE_DOCKER_SANDBOX=1 before restart"
  fi
  export DEVOS_USE_DOCKER_SANDBOX=1
elif have bwrap || have bubblewrap; then
  echo "[ok] bubblewrap available — restricted isolation without Docker"
  echo "     (no env change required; isolation.py prefers bwrap when Docker off)"
elif have firejail; then
  echo "[ok] firejail available — restricted isolation"
else
  echo "[gap] No docker/bwrap/firejail found."
  echo "  Ubuntu: sudo apt-get install -y bubblewrap"
  echo "  Or install Docker and set DEVOS_USE_DOCKER_SANDBOX=1"
  echo "  Untrusted project commands (flutter pub get, npm, pytest) will"
  echo "  fail closed with isolation_unavailable until a strong backend exists."
  exit 1
fi

# Never enable degraded host for production
if [[ -f .env ]] && grep -q '^DEVOS_ALLOW_DEGRADED_ISOLATION=1' .env; then
  echo "WARN: DEVOS_ALLOW_DEGRADED_ISOLATION=1 is set — remove for production untrusted isolation"
fi

echo "=== probe ==="
PYTHONPATH=. python3 - <<'PY'
from execution.isolation import detect_backends
import json
print(json.dumps(detect_backends(), indent=2))
info = __import__("execution.isolation", fromlist=["detect_backends"]).detect_backends()
if not info.get("suitable_for_untrusted_code"):
    raise SystemExit("still not suitable_for_untrusted_code — install bwrap or enable Docker")
print("suitable_for_untrusted_code: true")
PY

echo "Restart DevOS if running: systemctl restart devos"
echo "Verify: curl -fsS https://dev.carai.agency/api/health | jq .isolation"
