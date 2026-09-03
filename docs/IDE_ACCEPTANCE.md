# DEVOS IDE Acceptance Report

**Baseline:** `47cc3e4`  
**Acceptance closeout commit:** (this commit)  
**Date:** 2026-09-03

## Verdict

**IDE NOT COMPLETE**

Python governance / recovery / ExecutionOperation / PTY / acceptance fixtures are **PASS** in this environment after installing SQLAlchemy stack from official PyPI.

Frontend production build remains **BLOCKED** (Node 24 vs engines `<23`, incomplete `npm install` under 1.2 GiB RAM — `react-scripts` not present).

Automation / n8n audit is **not** started.

## Classification

| Status | Meaning |
|--------|---------|
| PASS | Verified by tests and/or code inspection |
| FAIL | Code defect |
| BLOCKED | Environment prevented verification |
| SKIPPED | Explicitly out of scope |

## Architecture invariants

| Invariant | Status |
|-----------|--------|
| Single AgentRuntime → HAI → UCIP → ExecutionOperation → Evidence | PASS |
| No second event architecture | PASS |
| Stage 3N cancel / after_seq | PASS |
| Stage 3K.1 verification ≠ exit 0 | PASS |
| Stage 3L process recovery | PASS (`test_hai_process_recovery` 14) |
| Stage 3M ExecutionOperation ledger | PASS |
| UNKNOWN no blind retry | PASS (execution_operations suite) |
| Tenant isolation / secret scrubbing paths | PASS (security suites) |

## Pytest matrix (this environment)

| Suite | Result |
|-------|--------|
| test_agent_ide_loop.py | PASS |
| test_agent_tools.py | PASS |
| test_hai_control.py | PASS |
| test_hai_process_recovery.py | PASS |
| test_execution_operations.py | PASS |
| test_execution_operation_security.py | PASS |
| test_execution_operation_concurrency.py | PASS |
| test_ide_file_security.py | PASS |
| test_ide_acceptance_fixtures.py | PASS |
| test_agent_changes_and_intel.py | PASS |
| test_lsp_manager.py | PASS |
| test_pty_session.py | PASS |

Combined runnable agent/IDE/governance matrix: **all collected tests PASS** after installing `sqlalchemy`, `aiosqlite`, `pytest-asyncio`, `pydantic-settings` from `https://pypi.org/simple/`.

## Frontend build

| Step | Result |
|------|--------|
| Node version | v24.15.0 (required: >=22 <23) |
| npm install | Incomplete under 1.2 GiB RAM; `react-scripts` missing |
| npm run build | **BLOCKED** — cannot run without `react-scripts` |

## Fixture A–H

| Fixture | Status | Evidence |
|---------|--------|----------|
| A Normal inspect/edit | PASS | test_fixture_a_inspect_edit_cycle |
| B Failure + repair semantics | PASS | test_fixture_b_* (structured test_result vs exit 0; related tests bounded; repair write) |
| C Patch conflict / stale | PASS | apply conflict + stale reject |
| D Cancellation | PASS | cancel flag + durable cancel tests |
| E after_seq replay | PASS | monotonic seq + after_seq |
| F Process crash recovery | PASS | test_hai_process_recovery (14) |
| G UNKNOWN no blind retry | PASS | test_execution_operations UNKNOWN cases |
| H Isolation | PASS | task events + change user isolation |

## Remaining blockers to declare IDE COMPLETE

1. **Frontend production build PASS** on Node 22 with full `npm ci` / `npm run build` (≥4 GiB RAM recommended).
2. Optional: live E2E smoke with running API server (not required if build + matrix PASS, but recommended).

## What was fixed in closeout

- Installed missing Python test dependencies from official PyPI (mirror was 502).
- Fixture B added (structured verification semantics).
- Re-ran previously BLOCKED recovery, ExecutionOperation, PTY suites → **PASS**.

## Automation gate

Still closed. Do not implement n8n/Zapier parity until frontend build is PASS and this document’s verdict is **IDE COMPLETE**.
