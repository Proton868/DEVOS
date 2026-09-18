# Bounded Parallel Automation Execution

**Decision:** **READY_FOR_AGENTIC_AUTOMATION**

## Purpose

Execute independent workflow branches concurrently without a second engine.

```
         ┌── B ──┐
A ───────┤       ├── D
         └── C ──┘
```

After A succeeds, B and C are independently eligible and may run concurrently.
D waits for its join condition.

## Fan-out model

1. `select_eligible_steps(snap, state)` returns a deterministic list (e.g. `[B, C]`).
2. `select_schedulable_steps` applies the per-run concurrency cap.
3. Each step is executed via `run_from_snapshot(..., max_steps=1)` — the sole consequential executor.
4. Logical identity: `step_operation_idempotency_key(run_id, version, step_id, occurrence)`.

Parallelism is **orchestration scheduling**, not a new worker pool.

## Fan-in / join model

Declared joins:

```json
{"target": "D", "deps": ["B", "C"], "mode": "all_success"}
```

| Mode | D eligible when |
|------|-----------------|
| `all_success` | Every dep is SUCCEEDED |
| `any_success` | At least one dep is SUCCEEDED |

UNKNOWN on any step pauses **all** eligibility (`select_eligible_steps` → `[]`).
UNKNOWN is never treated as failure for automatic routing.

## Scheduler ownership

- **Orchestrator** (`automation_orchestration`): eligibility + joins.
- **Parallel layer** (`automation_parallel`): wave scheduling, merge, concurrency.
- **Runtime** (`automation_runtime`): loads immutable snapshot, drives parallel graph, projects `AutomationRunRecord`.
- **Executor** (`workflow_executor`): step body (SCRIPT isolation, HTTP credentials, …).

## Operation / job relationship

| Entity | Role |
|--------|------|
| Automation run | Parent projection |
| Step | Logical unit |
| Operation key | `sha256(run, version, step, occurrence)` |
| ExecutionJob | Durable claim unit (existing queue) |

One giant “parallel job” is forbidden. Independent steps get independent identities.

## Concurrency limits

| Scope | Value |
|-------|--------|
| **Per automation run** | Default **4** (`DEVOS_AUTOMATION_MAX_PARALLEL`, ceiling 32) |

Not global fairness, not per-tenant rate limiting. Prevents unbounded fan-out storms.

## Duplicate scheduling protection

- Terminal steps are never re-eligible.
- Same `step_operation_idempotency_key` for the same logical step.
- Wave merge is idempotent for SUCCEEDED records.
- Job enqueue (when used) retains tenant+idempotency_key semantics.

## Worker failure / UNKNOWN

- Existing ExecutionJob lease recovery applies to step jobs.
- UNKNOWN → permanent, no auto-redispatch.
- Branch B UNKNOWN does not imply C failure; join still blocked.
- D cannot run solely because C succeeded if ALL_SUCCESS and B is UNKNOWN/failed.

## Cancellation

Uses existing run cancellation: pending waves stop when eligibility is empty after cancel flag; no second cancellation state machine.

## Restart semantics

| Case | Behavior |
|------|----------|
| Before fan-out | B/C scheduled once from durable records |
| After enqueue | Terminal B stays terminal; C continues |
| B done, C running | Independent reconcile; D blocked |
| B UNKNOWN | No B redispatch; graph paused |
| Both done | D eligible exactly once |

Authoritative state is step records + immutable snapshot; `AutomationRunRecord` is a projection.

## Security contracts (unchanged)

- SCRIPT → isolation only  
- HTTP → credential refs only  
- DATABASE → existing authorization  
- TRANSFORM → pure  
- LOOP → bounded  

Parallel scheduling cannot bypass UCIP.

## PostgreSQL concurrency

Operation/job idempotency remains tenant-scoped unique keys on the existing ledger. Parallel waves do not introduce a second dedup store.

## Observability

Run state / context `_parallel` records:

- scheduled step ids  
- concurrency policy  
- operation keys for the wave  

Plus existing operation_id / job_id on the run projection.
