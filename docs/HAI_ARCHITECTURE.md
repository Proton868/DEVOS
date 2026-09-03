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
