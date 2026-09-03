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

---

## Official freeze

| Layer | Status |
|-------|--------|
| Governance v1 | **FROZEN** |
| Reliability architecture v1 | **FROZEN** |
| Architecture audit | **PAUSED** |

Do not change design to “pass” a drill. Fix implementation bugs; reopen architecture only if an invariant is violated.

## Result capture (required per drill)

| Field | Value |
|-------|--------|
| Drill name | |
| EXPECTED | |
| ACTUAL | |
| PASS/FAIL | |
| request_id / correlation | |
| execution_job_id | |
| Database state | |
| Job status | |
| Evidence state | |
| Side-effect outcome (SUCCEEDED/FAILED/UNKNOWN) | |
| Recovery time | |
| Notes | |

## Awkward-boundary cases (must include)

Do not only test clean kills. Prefer:

| Awkward failure | Risk |
|-----------------|------|
| DB commits, response lost to client | Duplicate submit / orphan job |
| Response arrives, process dies before local complete | Late complete vs reclaim |
| Provider succeeds, DB write fails | UNKNOWN until reconcile |
| Lease expires ~1s before completion | Dual complete race |
| Redis disappears during quota check | Multi-node fail-closed vs drift |
| Worker dies after external request sent | UNKNOWN + no blind retry |
| Migration dies after some statements | `migrate_resume.py` coherence |
| Two workers start simultaneously | Single logical owner |

## Sequence (Production Readiness Gate v1)

1. PostgreSQL failure injection  
2. Worker crash / lease recovery  
3. Two-worker race  
4. Idempotency replay  
5. Drop-response external provider  
6. UNKNOWN → reconciliation  
7. Redis failure  
8. Multi-node quota behavior  
9. Interrupted migration  
10. Full deployment checklist  

**FAIL → STOP.** No silent override in a developer shell for production promote.


## Live P0 — worker kill after claim (executable)

**Status:** harness + production recovery proven (3/3 PASS on PostgreSQL + Redis + 2 workers + SIGKILL).

### Expected recovery

```
attempt 1 → claim → SIGKILL → lease expiry
  → surviving worker claim/recovery (production claim_next)
  → attempt 2 → operation succeeds → job succeeds
```

### Valid recovery proof (authoritative durable state)

| Field | Required |
|-------|----------|
| `initial_attempt` | `1` |
| `recovery_attempt` | `2` |
| `recovery_worker` | ≠ `killed_worker` |
| `final_job.status` | `succeeded` |
| `operation.status` | `succeeded` |
| `job_rows_for_idempotency_key` | `1` |
| `duplicate_operations` | `0` |
| `duplicate_evidence` | `0` |
| `blind_unknown_retry` | `false` |

`recover_stale_leases_count` is **diagnostic only**. It MUST NOT be the sole recovery assertion. Recovery often occurs inside the survivor's `claim_next` path (count may be `null` / unused by the harness).

### Stop conditions

**BLOCKED (exit 2)** — infrastructure:

- `DATABASE_URL` is not PostgreSQL
- PostgreSQL unreachable / schema init failure
- Redis configured but unreachable
- Worker A or B failed to start

**FAIL (exit 1)** — drill:

- job never claimed / wrong claimer
- SIGKILL failed
- lease recovery not observed (`attempts` never reaches 2 under survivor)
- final status not `succeeded`
- idempotency or duplicate operation/evidence violated
- blind UNKNOWN retry

**PASS (exit 0)** — all durable assertions above hold.

Timeouts never become PASS. Every wait has a deadline and phase-tagged failure.

### Diagnosis vs action

| Role | Allowed |
|------|---------|
| **Diagnosis** | Observe PG job/operation state, workers, Redis; never force recovery |
| **Action** | Start workers, create one test job, SIGKILL claimer |

The harness does **not** call `recover_stale_leases` to force a pass.

### Prerequisites & run

```bash
# infra: docker compose -f docker-compose.staging.yml up -d postgres redis
# or host PostgreSQL + Redis
export DATABASE_URL=postgresql+asyncpg://devos:devos_password@127.0.0.1:5432/devos
export REDIS_URL=redis://127.0.0.1:6379
export DEVOS_JOB_LEASE_S=3
export DEVOS_STAGING_HOLD_S=5
python scripts/staging_p0_worker_kill.py --lease-s 3
```

Evidence: `data/staging-results/p0-worker-kill-*.json` (not committed).

Standalone worker: `scripts/run_job_worker.py` (does not load `app.py`).




## Live P1 — external provider drop-response → UNKNOWN → reconciliation

**Purpose:** Prove that when a provider accepts a side effect but the response is lost, DEVOS records **UNKNOWN**, does **not** blind-retry, and reconciliation discovers provider truth with **exactly one** external side effect.

**Topology:** PostgreSQL + Redis + independent provider stub + worker-a + worker-b.

**Provider failure mode:** `POST /execute` records acceptance, then drops the connection. `GET /status/{key}` exposes provider truth.

**Expected state machine:**

```
provider accepted → response lost → UNKNOWN → no retry → reconciliation → succeeded
provider.side_effect_count == 1 throughout
```

**P1 is not proven by a mock provider response.** It is proven only when an independent provider process records the external side effect, DEVOS records UNKNOWN after losing the response, no blind retry occurs, reconciliation discovers the actual provider state, and the operation reaches durable success with exactly one provider-side side effect.

### Stop conditions

| Outcome | Exit | Examples |
|---------|------|----------|
| BLOCKED | 2 | PG/Redis/provider/workers unavailable |
| FAIL | 1 | No UNKNOWN, blind retry, duplicate side effect, reconcile fail |
| PASS | 0 | All expected_state fields hold |

### Run

```bash
export DATABASE_URL=postgresql+asyncpg://devos:devos_password@127.0.0.1:5432/devos
export REDIS_URL=redis://127.0.0.1:6379
export P1_PROVIDER_URL=http://127.0.0.1:8099
python scripts/staging_p1_unknown_reconciliation.py
```

Evidence: `data/staging-results/p1-unknown-reconciliation-*.json`


## Automated pure-logic profile

```bash
python scripts/run_chaos_drills.py --out data/chaos/latest_report.json
./scripts/ci_deploy_gate.sh
```

Runs the algorithmic matrix (claim races, lease, UNKNOWN, drop-response mock,
secrets, multi-node quota fail-closed, inject points) with full JSON capture.

**Does not replace** live Postgres/Redis/worker drills — it gates CI on
failure semantics before staging infrastructure tests.
