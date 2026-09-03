# DEVOS HAI Architecture

## Validated recovery lifecycle (Stage 3I)

HAI checkpoint → process interruption → restore + checksum → ExecutionJob reconciliation → continue/verify/replan/block/unknown

**ExecutionJob remains authoritative over actual execution state.**

UNKNOWN: lifecycle unknown, retry=false always.

Acceptance fixture: tests/fixtures/auth_fail_project/
