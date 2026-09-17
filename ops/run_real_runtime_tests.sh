#!/usr/bin/env bash
# Real-runtime production execution gate.
# Forbids DEVOS_ORCH_FAKE_RUNTIME. Does not enable fake runtime.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export DEVOS_REAL_RUNTIME_TESTS=1
unset DEVOS_ORCH_FAKE_RUNTIME || true
unset DEVOS_ALLOW_FAKE_RUNTIME || true

# Default provider mode: scripted transport double (AgentRuntime stays real).
# Set DEVOS_REAL_RUNTIME_PROVIDER=free to use OmniRoute/OpenRouter free models.
export DEVOS_REAL_RUNTIME_PROVIDER="${DEVOS_REAL_RUNTIME_PROVIDER:-scripted}"

if [[ "${DEVOS_ORCH_FAKE_RUNTIME:-}" == "1" ]]; then
  echo "real_runtime_gate_failed: DEVOS_ORCH_FAKE_RUNTIME=1 is forbidden" >&2
  exit 2
fi

echo "========== REAL-RUNTIME GATE =========="
echo "DEVOS_REAL_RUNTIME_TESTS=$DEVOS_REAL_RUNTIME_TESTS"
echo "DEVOS_ORCH_FAKE_RUNTIME=${DEVOS_ORCH_FAKE_RUNTIME:-}"
echo "DEVOS_REAL_RUNTIME_PROVIDER=$DEVOS_REAL_RUNTIME_PROVIDER"
echo "DEVOS_USE_DOCKER_SANDBOX=${DEVOS_USE_DOCKER_SANDBOX:-}"
echo "======================================="

exec python -m pytest tests/real_runtime/ -m real_runtime -v --tb=short "$@"
