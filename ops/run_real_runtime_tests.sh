#!/usr/bin/env bash
# Canonical real-runtime production execution gate.
#
# PASS means:
#   - DEVOS_ORCH_FAKE_RUNTIME was not enabled
#   - real AgentRuntime / UCIP / FileService modules were loaded
#   - workspace file tools exercised the real path (not _fake_runtime)
#   - acceptance/evidence/isolation checks used production evaluation code
#   - provider may be scripted at the *transport* boundary only
#
# Exit codes:
#   0  all real_runtime tests passed
#   1  pytest test failure(s)
#   2  configuration error: fake runtime enabled or gate precondition failed
#   3  missing prerequisites (reported as pytest.fail inside suite)
#
# Does NOT enable fake runtime. Does NOT claim live LLM proof when
# DEVOS_REAL_RUNTIME_PROVIDER=scripted (default).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export DEVOS_REAL_RUNTIME_TESTS=1
unset DEVOS_ORCH_FAKE_RUNTIME || true
unset DEVOS_ALLOW_FAKE_RUNTIME || true

export DEVOS_REAL_RUNTIME_PROVIDER="${DEVOS_REAL_RUNTIME_PROVIDER:-scripted}"

if [[ "${DEVOS_ORCH_FAKE_RUNTIME:-}" == "1" ]]; then
  echo "real_runtime_gate_failed: DEVOS_ORCH_FAKE_RUNTIME=1 is forbidden" >&2
  exit 2
fi
if [[ "${DEVOS_ALLOW_FAKE_RUNTIME:-}" == "1" ]]; then
  echo "real_runtime_gate_failed: DEVOS_ALLOW_FAKE_RUNTIME=1 is forbidden" >&2
  exit 2
fi

echo "========== REAL-RUNTIME GATE =========="
echo "DEVOS_REAL_RUNTIME_TESTS=$DEVOS_REAL_RUNTIME_TESTS"
echo "DEVOS_ORCH_FAKE_RUNTIME=${DEVOS_ORCH_FAKE_RUNTIME:-}"
echo "DEVOS_ALLOW_FAKE_RUNTIME=${DEVOS_ALLOW_FAKE_RUNTIME:-}"
echo "DEVOS_REAL_RUNTIME_PROVIDER=$DEVOS_REAL_RUNTIME_PROVIDER"
echo "PASS requires: real AgentRuntime+UCIP+workspace; fake runtime forbidden"
echo "======================================="

set +e
python -m pytest tests/real_runtime/ -m real_runtime -v --tb=short "$@"
code=$?
set -e
if [[ $code -ne 0 ]]; then
  echo "real_runtime_gate_failed: pytest exit=$code" >&2
  exit $code
fi
echo "real_runtime_gate_passed"
exit 0
