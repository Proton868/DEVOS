#!/usr/bin/env bash
# CI deploy gate: unit freezes + production checklist must not BLOCK.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== governance freeze + reliability unit tests =="
python -m pytest tests/test_governance_freeze.py tests/test_reliability.py tests/test_failure_drills.py -q

echo "== production checklist =="
# In CI without secrets, checklist may FAIL on JWT — allow DEVOS_CHECKLIST_SOFT=1 for non-prod
if [[ "${DEVOS_CHECKLIST_SOFT:-}" == "1" ]]; then
  python scripts/production_checklist.py || true
  echo "SOFT gate: checklist failures non-blocking"
else
  python scripts/production_checklist.py
fi

echo "CI deploy gate passed"
