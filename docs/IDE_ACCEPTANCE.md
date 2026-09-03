# DEVOS IDE Acceptance Report

**Baseline:** `4d92fc9`  
**Acceptance commit:** (this commit)  
**Date:** 2026-09-03

## Verdict

**NOT COMPLETE** — substantial production-grade surfaces exist and are tested where the environment allows, but the full acceptance gate is **not** satisfied. Automation/n8n audit is **not** started.

## Classification legend

- **PASS** — verified by code inspection and/or tests in this environment  
- **FAIL** — code defect found  
- **BLOCKED** — environment prevented verification  
- **SKIPPED** — explicitly unsupported / out of scope  

## Architecture invariants

| Invariant | Status |
|-----------|--------|
| Single AgentRuntime → HAI → UCIP → ExecutionOperation → Evidence | PASS (preserved) |
| No second event architecture | PASS |
| Stage 3N cancel / after_seq | PASS (unit tests) |
| Stage 3K.1 verification ≠ exit 0 | PASS (runtime messages + tool design) |
| Tenant / project scoped files | PASS |
| Absolute path / traversal fail-closed | PASS (`test_ide_file_security`) |

## Subsystem results

| Area | Status | Notes |
|------|--------|-------|
| Editor tabs / dirty / reopen / save | PASS | Store + CodeEditor confirmation |
| FileTree lazy load + keyboard | PASS | depth=1 + listDir; Arrow nav |
| DiffViewer accept/reject/revert | PASS | Stale protection added on reject/revert |
| Patch conflict on apply | PASS | `apply_unified_diff` / context replace |
| Concurrent user edit vs reject | PASS | Fixture C — refuses overwrite |
| Problems → Monaco line jump | PASS | `devos:goto-line` |
| Command registry (single) | PASS | registerCoreCommands + palette |
| Agent panel modes / cancel / reconnect | PASS (code) | Durable stream consumption present |
| LSP manager backend | PASS (code) | Completions/hover/def/refs/rename wired in manager |
| LSP frontend full surface | SKIPPED / partial | Manager exists; not all IDE UI actions proven E2E |
| Terminal PTY governed | BLOCKED | `pytest_asyncio` missing; code present |
| Git destructive ops gated | PASS (code review) | No force-push UI path |
| Frontend production build | BLOCKED | `react-scripts` missing after incomplete npm install; Node 24 vs engines <23; 1.2GiB RAM |
| Durable DB cancel/append_event | BLOCKED | SQLAlchemy not installable (PyPI 502) |
| Process recovery Stage 3L | BLOCKED | SQLAlchemy |
| ExecutionOperation security/concurrency | BLOCKED | SQLAlchemy |
| Full multi-user live concurrency | BLOCKED | No multi-process harness in this env |
| Automation matrix | SKIPPED | Gated on IDE complete |

## A–H acceptance fixtures

| Fixture | Status | Evidence |
|---------|--------|----------|
| A Normal inspect/edit | PASS | `test_fixture_a_inspect_edit_cycle` |
| B Failure and repair | SKIPPED | Needs live HAI + test runner E2E |
| C Patch conflict / stale | PASS | Apply conflict + stale reject tests |
| D Cancellation | PASS | `test_fixture_d_cancel_sets_flag` + ide_loop |
| E SSE / after_seq | PASS | `test_fixture_e_after_seq_replay_no_duplicates` |
| F Process crash recovery | BLOCKED | SQLAlchemy / recovery suite |
| G UNKNOWN no blind retry | BLOCKED | ExecutionOperation suite |
| H Isolation | PASS | Event + change user isolation tests |

## Tests run this pass

```
pytest tests/test_agent_ide_loop.py tests/test_agent_tools.py \
  tests/test_hai_control.py tests/test_ide_file_security.py \
  tests/test_ide_acceptance_fixtures.py tests/test_agent_changes_and_intel.py \
  tests/test_lsp_manager.py -q
→ all collected tests PASSED (3 skipped for optional SQLAlchemy paths in ide_loop)
```

BLOCKED collections (environment):

- `test_hai_process_recovery.py`
- `test_execution_operations.py`
- `test_execution_operation_security.py`
- `test_execution_operation_concurrency.py`
- `test_pty_session.py` (pytest_asyncio)

## Code changes in acceptance pass

1. **Stale-content protection** on `revert_change` / `reject_change` — compares disk hash to `after_hash`; returns `{stale: true}` without overwrite unless `force=True`.
2. **Acceptance fixtures** `tests/test_ide_acceptance_fixtures.py` (C, D, E, H, A, security cross-checks).
3. **FakeFS.read** for existing agent-change unit tests.
4. This document.

## Honest completion gate

Cannot declare **IDE COMPLETE** until:

1. Frontend `npm run build` succeeds on a supported Node version with full install.
2. SQLAlchemy-backed recovery / ExecutionOperation / durable cancel suites PASS.
3. Fixtures B, F, G proven with live or integration harness.
4. Terminal PTY tests PASS with pytest_asyncio.

Until then: **production-grade foundation exists; acceptance gate NOT closed.**
