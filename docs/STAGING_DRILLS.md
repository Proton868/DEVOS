# Production Readiness Gate v1 — Staging Drill Matrix

Governance v1 and Reliability architecture v1 are **frozen**.
This document defines live infrastructure drills. CI state-machine tests prove
*algorithmic* semantics only — not Postgres isolation, Redis partitions, or process kills.

## Pipeline

```
commit → unit/freeze/reliability/drills → checklist → image → staging
      → live failure drills → approval → production
FAIL → STOP
```

## P0 — PostgreSQL process kill matrix

Kill the worker (SIGKILL) after each step; restart; assert final state.

| Crash after | Expected recovery |
|-------------|-------------------|
| job created | job remains `queued`; no side effect |
| job claimed | lease expires → reclaim by another worker |
| execution started | lease recovery; no duplicate logical success |
| external call started | effect may be UNKNOWN until reconcile |
| external call returned | result may be lost → UNKNOWN path |
| result persisted | complete resumes; evidence if missing |
| evidence persisted | trust/learn may lag; no authority corruption |
| completion persisted | terminal; late completes ignored |

## P0 — Two real workers

```
worker-A + worker-B → same Postgres (+ Redis if multi-node)
Hammer identical job / idempotency key
Assert: N workers → 1 logical owner; at most one SUCCEEDED
```

## P0 — External provider (drop response)

Test provider: accept request, perform side effect, **drop HTTP response**.

```
DevOS sees timeout → UNKNOWN
reconcile() finds effect → SUCCEEDED, retry=false
reconcile() finds nothing → FAILED, retry allowed
```

HTTP 500 after possible processing must be treated as **UNKNOWN**, not FAILED.

## P1 — Redis

| Mode | Redis down | Expected |
|------|------------|----------|
| single-node | yes | conservative memory quotas |
| multi-node (`DEVOS_MULTI_NODE=1`) | yes | expensive quotas **fail closed** |
| multi-node | Redis recovers mid-flight | jobs continue; quotas resume |

## P1 — Migration interrupt

```
migrate → SIGKILL → migrate_resume.py → schema valid + data intact → app starts
```

Run against production-shaped data, not empty DB.

## P1 — Checklist gate

```bash
./scripts/ci_deploy_gate.sh          # CI
python scripts/production_checklist.py  # must not BLOCK for prod
```

`DEVOS_FAIL_INJECT` set → checklist **FAIL** / deployment **BLOCKED**.

## Run failure injection on staging

```bash
export DEVOS_FAIL_INJECT=after_job_claim
# restart workers, exercise one job, unset inject, recover
```
