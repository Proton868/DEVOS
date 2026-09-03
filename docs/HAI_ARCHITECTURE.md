# DEVOS HAI Architecture

## Validated recovery lifecycle (Stage 3I)

HAI checkpoint → process interruption → restore + checksum → ExecutionJob reconciliation → continue/verify/replan/block/unknown

**ExecutionJob remains authoritative over actual execution state.**

UNKNOWN: lifecycle unknown, retry=false always.

Acceptance fixture: tests/fixtures/auth_fail_project/

## Stage 3J — Unified control

Agent mode initializes `StrategicController` inside the existing `AgentRuntime` loop.
No second agent runtime. Coordinator and Workflow engines are delegation targets only.
MAX_STEPS exhaustion reports `blocked` with `success: false`.

## Stage 3K — Verification authority

HAI controls cognitive lifecycle and may **veto** natural-language completion when verification is outstanding.

- Consequential edits → `verification_required`
- `run_tests` / verification tools: **actual test outcome** (exit code / structured flags), not merely `ok=True`
- Repeated-action detection uses tool + normalized args + subgoal + outcome class
- REPLAN injects updated plan into the next reasoning cycle
- Consequential checkpoint persistence failure → BLOCK (not silent continue)

Stage 3J = checkpoint/reconciliation infrastructure.
Stage 3K = verification-authoritative AgentRuntime control.
Future = true process crash/restart, GoalDecomposer-backed planning, full Coordinator execution.
