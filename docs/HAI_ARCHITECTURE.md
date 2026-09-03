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

## Stage 3K.1 — Verification semantics

Tool execution success and verification success are distinct. Generic `run_command` success (including `exit_code=0`) is **not** sufficient evidence that a consequential edit is correct. HAI requires explicit or recognized verification evidence (`tests_passed`, `verification_passed`, or clear test summary output from `run_tests`) before treating verification as passed.

## Stage 3L — Process-level crash recovery

Durable recovery boundary:

- AgentTaskRecord (identity, status, correlation_id, events)
- HAICheckpoint (cognitive state, checksum)
- ExecutionJob (execution truth)
- Evidence (proof)

Recovery semantics:

| Durable job/task state | Recovery action |
|------------------------|-----------------|
| queued / running | WAIT — do not duplicate execution |
| succeeded | continue HAI / verification — do not rerun |
| failed | replan recommendation — retry=false |
| unknown | investigate — never blind retry |
| cancelled | terminal — no execution |
| corrupt checkpoint | fail closed — no execution |

Recovery lease: only one worker may own recovery of a task at a time (`claim_task_recovery`).

Security: checkpoint is cognitive state, not authority. Identity comes from AgentTaskRecord columns only.

Entrypoint: `brain/hai_recovery.recover_hai_task` — restores and reconciles; does not execute tools.
