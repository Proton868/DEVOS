# Durable Execution Integrity Audit

**HEAD audited:** `8c26bc7b741b6a389485d2de91f4217bbd45c183`  
**Date:** 2026-09-18  
**Mode:** Read-only first; production changes only if a concrete RED defect is found.  
**Outcome:** **READY_FOR_NEXT_CAPABILITY**

---

## Scope

Audit of the durable consequential execution spine:

```
Intent → Capability → Authorization → ExecutionJob → ExecutionOperation
  → Worker claim → Isolation → Side effect → Evidence → Verification
  → Job/Operation terminal state → Resume/Reconciliation
```

In scope modules:

- `governance/capability_substrate.py`
- `governance/execution_operations.py`
- `governance/execution_pipeline.py`
- `workers/job_queue.py`
- `execution/durable_resume.py`
- `execution/isolation.py`
- `execution/app_runtime.py`
- `brain/orchestration_dag.py`
- `brain/mission_engine.py`
- evidence persistence (`EvidenceRecord`, `record_path_evidence`, digests)
- migrations for operation idempotency uniqueness
- execution-integrity tests listed in the Verification Matrix

Out of scope: Spatial OS redesign, Nuha policy content, full deployment provider inventory, cosmetic refactors.

---

## Current Architecture

| Authority | Responsibility |
|-----------|----------------|
| **UCIP + CapabilityRegistry + CapabilitySubstrate** | Authorization before consequential execution |
| **ExecutionOperation** | Authoritative consequential operation ledger (RESERVED→RUNNING→terminal) |
| **ExecutionJob** | Durable work unit + lease ownership; bound to operation for consequential types |
| **EvidenceRecord** | Durable outcome evidence linked by `operation_id` |
| **OrchestrationPlan / DAG** | Projection of work topology; consumes op/job/evidence; not a second ledger |
| **execution/isolation.py** | Canonical isolation for untrusted/project code (`run_isolated` / `spawn_isolated`) |

Canonical rules already encoded in code and tests:

- Consequential `enqueue` reserves operation in the **same transaction** as job create.
- Idempotency identity: `owner_id + operation_type + idempotency_key + COALESCE(tenant_id,'')` (partial unique index).
- UNKNOWN is investigate-only (`execute=false`, `retry=false`); DAG maps to `pending_review`.
- `complete()` rejects mismatched `worker_id` when job is running under a lease owner.

---

## Invariants Audited

### Authorization

**Path:** `CapabilitySubstrate.invoke` → `authorize()` → `authorize_capability_slug` → validation → executor (or dry-run stop).

| Check | Result |
|-------|--------|
| Authorization before executor | `invoke` returns DENIED before executor; dry-run never calls executor |
| Client-supplied grants rejected | `client_supplied_grants=True` → `client_supplied_grants_rejected` |
| Fail closed on authorize errors | `authorize_error:*` |
| Substrate non-goal | Surfaces may request; cannot elevate by inventing grants |

**Evidence:** `tests/test_capability_substrate.py`, `tests/test_successful_execution_e2e.py` (deny → no side effect).

**Note:** Not every historical entrypoint is forced through the substrate (AgentRuntime tools, governed_exec). Those paths use UCIP/tool contracts and isolation. Substrate is the unified contract for surfaces adopting it; residual alternate entrypoints are **AMBER** (consistency), not proven unauthorized bypass of UCIP for agent tools.

### Operation Identity

| Check | Result |
|-------|--------|
| One authoritative op per consequential enqueue | `reserve_operation_tx` in same txn as job; fails closed if reservation fails |
| Durable identity | Postgres/SQLite `execution_operations` |
| Idempotency scope | Unique index + IntegrityError reselect |
| Concurrent same key | Postgres: one row, all callers converge |
| Args not uniqueness axis | Documented contract; callers must encode logical identity in the key |

**Evidence:** `tests/test_job_operation_atomicity.py`, `tests/test_operation_idempotency_unique.py`, `tests/test_postgres_execution_concurrency.py`, `docs/architecture/operation-idempotency-contract.md`.

### Job Identity

| Check | Result |
|-------|--------|
| Consequential job binds `operation_id` | First-class column + payload mirror; binding validated |
| Single-owner claim | CAS `queued→running`; Postgres `FOR UPDATE SKIP LOCKED` |
| Stale worker cannot complete another’s job | `complete(..., worker_id=)` ownership gate |
| UNKNOWN op not requeued | Stale lease sets job failed, `retryable: false` |
| RESERVED + stale | Safe requeue (side effect never started) |

**Evidence:** `tests/test_job_operation_atomicity.py` (window C, ownership), `tests/test_unknown_restart_convergence.py`, Postgres claim test.

### UNKNOWN

| Check | Result |
|-------|--------|
| RUNNING + no/invalid evidence → UNKNOWN | `reconcile_operation` |
| UNKNOWN → execute/retry false | Explicit in reconcile return |
| Stale job + UNKNOWN → not ordinary retry | Job terminal failed, non-retryable |
| DAG | `pending_review`; excluded from `get_ready_nodes` / `compute_readiness` |
| UNKNOWN ≠ FAILED for auto-recovery | No automatic recovery loop from UNKNOWN |

**Evidence:** `tests/test_unknown_restart_convergence.py`, `tests/test_restart_safe_recovery_boundaries.py`, `execution/durable_resume.py`.

### Evidence

| Check | Result |
|-------|--------|
| Durable `EvidenceRecord` with optional `operation_id` | Schema + writers |
| Op digests validated against evidence | `validate_operation_evidence` |
| Recovery metadata ≠ verification | `mark_node_verified` rejects recovery-only keys |
| Missing evidence ≠ success | RUNNING reconcile → UNKNOWN |
| Valid evidence can promote UNKNOWN → SUCCEEDED | Existing reconcile path |

**Evidence:** `governance/execution_operations.py`, `tests/test_successful_execution_e2e.py`, DAG verification tests.

### DAG Convergence

| Check | Result |
|-------|--------|
| DAG is projection, not ledger | Nodes reference `job_or_task_id` / `operation_id`; resume consults op |
| SUCCEEDED + verification evidence → completed | Operation-aware resume + `mark_node_verified` |
| SUCCEEDED without DAG verification → pending_review | No fabricated VERIFIED |
| UNKNOWN → pending_review | No READY/PENDING redispatch |
| Completed not redispatched | Readiness skip lists + op-aware reconcile |
| Reconciliation idempotent | Proven in recovery and success E2E tests |

**Evidence:** `execution/durable_resume.py`, `tests/test_durable_resume_operation_aware.py`, `tests/test_successful_execution_e2e.py`.

### Isolation

| Check | Result |
|-------|--------|
| AppRuntime uses `spawn_isolated` + `POLICY_UNTRUSTED` | `execution/app_runtime.py` |
| Force-untrusted sources include `app_runtime` | Isolation policy classification |
| Fail closed when only network_only/unshare for untrusted | Policy tests |
| Canonical env sanitization | Shared `_sanitize_env` |
| Agent project commands | Documented path via `governed_exec` → isolation |
| Human IDE terminal | Explicit non-agent trust model (`execution/terminal.py`) — not agent path |

**Evidence:** `tests/test_app_runtime_security.py`, `tests/test_isolation_policy.py`, `tests/test_governed_exec.py`.

**AMBER residual:** Static inventory of every subprocess call site outside agent path (toolchains, LSP, tunnel) is operational/tooling trust, not untrusted project execution. Human terminal remains denylist-based by design.

### Forensic Completeness

| Field | Classification |
|-------|----------------|
| operation ID | **REQUIRED** — durable |
| job ID | **REQUIRED** — durable |
| owner / tenant | **REQUIRED** — durable on op/job |
| operation type | **REQUIRED** — durable |
| idempotency identity | **REQUIRED** when key present; **CONDITIONAL** if NULL (non-idempotent) |
| authorization / capability context | **CONDITIONAL** — on substrate invocation / pipeline path; not always copied onto op row |
| worker identity | **REQUIRED** while claimed; cleared on complete/recover |
| trust / source (isolation) | **CONDITIONAL** — isolation evidence on spawn/run; job `isolation` column optional |
| requested isolation | **CONDITIONAL** — in isolation decision objects |
| actual backend / strength | **CONDITIONAL** — SpawnResult / run result evidence; must be persisted by caller into job result for long-term forensics |
| start / end / outcome | **REQUIRED** — op + job timestamps/status |
| evidence linkage | **REQUIRED** for successful consequential close when evidence written (`evidence_id`) |
| failure / UNKNOWN / recovery context | **REQUIRED** — op.error, job.error JSON reason codes, DAG recovery_metadata |

**AMBER:** Capability/authorization snapshot is not a first-class column on `ExecutionOperation`; reconstruction relies on correlation/request logs and evidence body when present.

### Transaction Boundaries

| Boundary | Behavior |
|----------|----------|
| Enqueue | Single txn: reserve op + insert job + bind |
| Claim | Separate txn; CAS ownership |
| mark_running | Separate txn on operation |
| Side effect | Outside DB (correct) |
| Terminal op | Separate txn; conditional transitions |
| Evidence | Separate write |
| Job terminal | Separate; op-aware for consequential success |
| Stale lease | Op consulted before requeue |

No newly discovered window where two workers both produce a consequential side effect under current claim + ownership + UNKNOWN rules.

### Resume / Recovery

| State | Restart behavior |
|-------|------------------|
| RESERVED | Safe requeue / pending |
| RUNNING | Reconcile → evidence or UNKNOWN |
| UNKNOWN | Investigate; no auto execute |
| SUCCEEDED | Job converges; DAG does not redispatch |
| FAILED / CANCELLED | Terminal semantics; no blind retry |
| RECOVERING / REPLANNING | Persisted with plan; not ordinary READY work until recovery APIs advance |

---

## Findings

### GREEN

- Capability substrate denies without grants; rejects client-supplied grants; dry-run does not execute.
- Consequential job+operation creation is failure-atomic.
- Postgres unique idempotency index + concurrent reserve/claim proofs.
- Stale-worker `complete` ownership rejection.
- UNKNOWN fail-closed across op, job, DAG (`pending_review`).
- Operation-aware resume prevents redispatch of SUCCEEDED/UNKNOWN.
- Success E2E: one side effect, one op, one job, evidence, VERIFIED, dependent READY.
- AppRuntime forced untrusted isolation via `spawn_isolated`.
- Idempotency-key contract documented and tested (args not uniqueness axis).

### AMBER

| Area | Behavior | Why it matters | Evidence | Action |
|------|----------|----------------|----------|--------|
| Substrate not sole entry | Agent tools / pipelines may use UCIP without substrate | Consistency of auth surface | `capability_substrate.py` non-goals | Adopt substrate incrementally; do not treat as RED bypass without proven UCIP skip |
| Auth context on op row | Capability grant snapshot not always on `ExecutionOperation` | Forensic reconstruction | Schema | Optional correlation enrichment later |
| Isolation metadata on job | Backend/strength may live only in result JSON | Long-term audit of isolation | `complete(isolation=)` optional | Prefer always set isolation column on consequential complete |
| `complete` without `worker_id` | System/pipeline paths still allowed | Residual stale complete if caller omits id | `job_queue.complete` | Tighten when all callers pass worker_id |
| Human IDE terminal | Denylist, not full isolation | By design for human owner | `execution/terminal.py` | Keep agent path separate |
| SQLite vs Postgres | Most unit tests SQLite; concurrency proven on Postgres CI | Dialect differences | CI jobs | Keep both matrices |

### RED

**No concrete RED findings identified.**

No path found that:

- authorizes untrusted callers past UCIP/substrate denial for the substrate path,
- manufactures duplicate ops under the unique constraint,
- auto-retries UNKNOWN,
- fabricates VERIFIED from recovery metadata,
- allows AppRuntime project code to elevate to trusted host execution under current policy,
- or lets a second worker claim and execute the same consequential job under CAS + ownership checks.

---

## Required Remediation

**None for RED.**

Optional non-blocking follow-ups are listed under Deferred.

---

## Deferred / Non-Blocking Items

1. Enrich `ExecutionOperation` or evidence body with capability/authorization snapshot for forensics.
2. Require `worker_id` on all `complete()` calls for `running` jobs (breaking change for pipeline callers).
3. Broader substrate adoption for remaining tool entrypoints.
4. Optional Postgres CI expansion beyond concurrency module (full suite on PG).

---

## Verification Matrix

| Suite | Role | Status |
|-------|------|--------|
| `tests/test_capability_substrate.py` | Auth / dry-run / grants | Existing |
| `tests/test_operation_idempotency_unique.py` | Unique index / lifecycle | Existing |
| `tests/test_operation_idempotency_contract.py` | Key construction contract | Existing |
| `tests/test_postgres_execution_concurrency.py` | PG concurrent reserve + claim | Existing |
| `tests/test_job_operation_atomicity.py` | Crash windows + ownership | Existing |
| `tests/test_unknown_restart_convergence.py` | UNKNOWN cross-layer | Existing |
| `tests/test_restart_safe_recovery_boundaries.py` | pending_review / recovery persist | Existing |
| `tests/test_durable_resume_operation_aware.py` | Resume decision matrix | Existing |
| `tests/test_successful_execution_e2e.py` | Success convergence | Existing |
| `tests/test_app_runtime_security.py` / isolation policy | Isolation | Existing |

---

## Architectural Readiness

The durable execution spine is **internally consistent** for use as the foundation of the next DevOS capabilities:

- Authorization is fail-closed on the substrate path.
- Operation and job identities are durable, bound, and concurrency-safe under PostgreSQL proofs.
- UNKNOWN cannot be silently turned into success or ordinary retry.
- Evidence and DAG verification are separated from recovery metadata.
- Untrusted application runtime remains under canonical isolation policy.

Residual AMBER items are observability and adoption consistency, not known unsafe execution paths.

### Final decision

**READY_FOR_NEXT_CAPABILITY**
