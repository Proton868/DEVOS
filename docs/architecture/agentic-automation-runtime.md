# Durable Agentic Automation Runtime



## Completion Contract

An agent enters **COMPLETED** only when `validate_completion` passes.

Invariant (`agent_state == COMPLETED` requires):

1. Structured completion decision (`kind=complete`)
2. Durable checkpoint of the completing turn
3. No active consequential operation (`EXECUTING` forbidden)
4. No linked operation in UNKNOWN
5. No pending capability request in an active auth/exec state
6. All required consequential outcomes terminal (per contract)
7. Required evidence present when `required_evidence=True`
8. Required outputs present when listed
9. Task not cancelled
10. Contract requirements satisfied

Free-form text (`"done"`, `"verified"`) is **never** sufficient.

`CompletionContract` is immutable once bound on the task.

`try_complete` → COMPLETED on pass; remains PLANNING/BLOCKED on fail.

## Multi-Turn Execution

Scripted/production planner issues at most one capability request per turn.
After substrate execution, observation is checkpointed and becomes next-turn context.

## Recovery Test Taxonomy

| Layer | File | Owns |
|-------|------|------|
| State-machine unit | `tests/test_agentic_runtime.py` | transitions, bounds, cancel, UNKNOWN legality |
| Contract unit | `tests/test_agentic_automation.py` | delegation, grants, isolation |
| Multi-turn E2E | `tests/test_agentic_multiturn_e2e.py` | completion contract, restart, multi-turn path |

## Test Isolation and Cleanup

- Unique `owner-{uuid}` / `tenant-{uuid}` / idempotency keys per test
- Fixture resets process agent task store before/after
- No shared global `"test-user"` identities
- No dependency on external LLM, network, or production DB for E2E path

**Decision:** **READY_FOR_NEXT_AGENTIC_MILESTONE**

## Architecture

```
reason → inspect context → select capability request
  → UCIP / CapabilitySubstrate authorize
  → ExecutionOperation / ExecutionJob (consequential)
  → executor + isolation / credentials
  → evidence → structured observation
  → checkpoint → reason …
```

No second execution engine or job queue.

## Persisted state machine

```
CREATED → PLANNING ⇄ (AWAITING_CAPABILITY → AUTHORIZING → AUTHORIZED
          → EXECUTING → OBSERVING → CHECKPOINTING → PLANNING)
          → COMPLETED | FAILED | CANCELLED | BLOCKED
EXECUTING → UNKNOWN → BLOCKED | OBSERVING (reconcile only)
```

Illegal examples (rejected):

- `PLANNING → EXECUTING` (skips authorization)
- `UNKNOWN → EXECUTING` (no blind retry)
- `COMPLETED → *`

## Synchronous vs asynchronous

| Mode | Allowed |
|------|---------|
| **Sync** | Planning, context build, validation, transition, checkpoint |
| **Async / substrate** | Any consequential capability (mutate, network, script, DB, credentials) |

Long-running work links `operation_id` / `job_id` and leaves `EXECUTING` for worker resume via `observe_operation_result`.

## Turn model

- `DEVOS_AGENT_MAX_TURNS` (default 8, hard max 32)
- Default **1** capability request per turn
- Max capability requests per task (default 16, hard 64)
- Agent **cannot** raise bounds

## Checkpoint

Stored on `GovernedAgentTask.recovery.runtime_checkpoint` and optionally table
`agentic_runtime_checkpoints` (migration `20260918200000_agentic_runtime_checkpoints.sql`).

## UNKNOWN

`auto_retry=False`. Only `reconcile_unknown` may move to `OBSERVING` after authoritative resolution.

## Parallel automation

AGENT steps share the existing per-run concurrency limit (`DEVOS_AUTOMATION_MAX_PARALLEL`).

## Migration

### Forward (`20260918200000_agentic_runtime_checkpoints.sql`)

| Object | Detail |
|--------|--------|
| Table | `agentic_runtime_checkpoints` (PK `task_id`) |
| Columns | state machine fields, turn bounds, JSONB plan/observation/evidence, operation/job ids, `idempotency_key`, full `checkpoint` JSONB |
| Indexes | `owner_id`, `tenant_id`, `state`, `parent_run_id` |
| Unique | partial unique `(owner_id, idempotency_key)` where key present |
| Defaults | `state='created'`, `turn=0`, `max_turns=8`, empty JSON arrays |
| Side effects | **None** on existing tables |

Apply:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f supabase/migrations/20260918200000_agentic_runtime_checkpoints.sql
```

Re-run is safe (`IF NOT EXISTS`).

### Rollback (`20260918200000_agentic_runtime_checkpoints.down.sql`)

**Order:** drop unique index → drop secondary indexes → drop table.

**Data loss:** all rows in `agentic_runtime_checkpoints` only. Does not touch
`execution_operations`, `execution_jobs`, automation runs, or AgentTaskRecord.

**Before rollback:**

1. Confirm app revision does not require the table (or treats it as optional).
2. Optional backup:

```sql
COPY (SELECT * FROM agentic_runtime_checkpoints)
  TO '/tmp/agentic_runtime_checkpoints_backup.csv' WITH CSV HEADER;
```

3. Apply down file:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f supabase/migrations/20260918200000_agentic_runtime_checkpoints.down.sql
```


### Pre-rollback data verification

Run against production (or the target database) **before** applying the down migration:

| # | Check | SQL / action | Pass criteria |
|---|--------|--------------|---------------|
| V1 | Table present | `SELECT to_regclass('public.agentic_runtime_checkpoints');` | Understood (NULL = no-op drop) |
| V2 | Counts by state | `SELECT state, COUNT(*) … GROUP BY state` | Volume expected |
| V3 | In-flight rows | Rows where `state NOT IN ('completed','failed','cancelled')` | Empty **or** loss explicitly accepted |
| V4 | UNKNOWN / BLOCKED | `state IN ('unknown','blocked')` | Reviewed / exported |
| V5 | Linked ops sample | Rows with `operation_id` in executing/unknown/observing | Cross-checked if ops table exists |
| V6 | Owner/tenant footprint | `GROUP BY owner_id, tenant_id` | Isolation sample OK |
| V7 | Backup | `COPY (SELECT * FROM …) TO '…csv'` | File non-empty if `total_rows > 0` |

Do **not** proceed with `DROP TABLE` until V3/V4 are acknowledged and V7 backup exists when the table holds data.

Checklist is also embedded in `20260918200000_agentic_runtime_checkpoints.down.sql`.

**After rollback:**

```sql
SELECT to_regclass('public.agentic_runtime_checkpoints');  -- NULL
```

**Re-create:** re-run the forward migration; row data is not restored without backup.

**Compatibility:** additive forward allows old app + new schema during rollout.
Process store / `GovernedAgentTask.recovery.runtime_checkpoint` remains the
in-process durability path if the table is missing.


## Security

- Allowlist fixed at delegation
- No self-authorization
- Secrets scrubbed from context
- Fabricated evidence ignored
- Cross-owner/tenant denied via `assert_owner`


## Test Coverage Matrix

Maps the multi-turn E2E milestone checklist to owning tests.
Status: **covered** | **partial** | **gap**.

### Completion contract

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 1 | Valid completion | covered | `test_valid_completion` |
| 2 | Missing evidence | covered | `test_missing_evidence_blocks_completion` |
| 3 | Active operation | covered | `test_active_operation_blocks_completion` |
| 4 | UNKNOWN operation | covered | `test_unknown_blocks_completion` |
| 5 | Missing required output | covered | `test_missing_required_output_blocks` |
| 6 | Cancelled task | covered | `test_cancelled_blocks_completion` |
| 7 | Fake textual completion | covered | `test_fake_textual_completion_rejected` |
| 8 | Immutable completion requirements | covered | `test_completion_contract_immutable` |

### Multi-turn

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 9 | Two-turn successful agent | covered | `test_two_turn_successful_agent` |
| 10 | Three-turn path | covered | `test_three_turn_path_with_context` |
| 11 | Operation result → next-turn context | covered | `test_three_turn_path_with_context` (observations list) |
| 12 | Durable turn counter | covered | `test_durable_turn_counter_increments` |
| 13 | Max-turn enforcement | covered | `test_max_turn_enforcement`, `test_max_turns_blocks` |

### Restart / recovery

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 14 | Restart between turns | covered | `test_restart_between_turns` |
| 15 | Restart during operation | partial | `test_observe_operation_after_executing` + reload pattern; no full process-kill harness |
| 16 | Restart after op before completion | covered | `test_restart_after_operation_before_completion` |
| 17 | Restart after completion request before terminal commit | gap | not yet isolated as its own case |

### UNKNOWN

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 18 | UNKNOWN blocks completion | covered | `test_unknown_blocks_completion`, `test_unknown_does_not_complete_or_retry` |
| 19 | UNKNOWN does not retry | covered | `test_unknown_blocks_retry`, `test_unknown_no_auto_retry` |
| 20 | Authoritative reconciliation resumes | covered | `test_reconcile_then_complete`, `test_reconcile_unknown_to_observing` |

### UCIP / execution substrate

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 21 | Capability request reaches UCIP/substrate | covered | `test_capability_not_delegated_denied`, multi-turn via `request_capability` |
| 22 | Operation created | partial | depends on substrate metadata; not always asserted |
| 23 | Job created where required | gap | meta `devos.capability.list` often has no ExecutionJob |
| 24 | Existing executor performs work | partial | substrate invoke path; not SCRIPT/HTTP job worker E2E |
| 25 | Evidence returned | covered | synthetic/substrate evidence in multi-turn + `test_two_turn_successful_agent` |

### Parallel automation

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 26 | AGENT + normal step fan-out | covered | `test_agent_parallel_with_transform` |
| 27 | Join waits for actual agent completion | partial | join after AGENT SUCCEEDED; not explicitly against fake-complete |
| 28 | Fake agent completion does not release join | partial | `test_fake_complete_does_not_satisfy_join_contract` (validation gate; full workflow join fixture still open) |

### Test isolation

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 29 | Unique namespaces | covered | `test_tests_use_unique_namespaces`, `_ns()` helper |
| 30 | Cleanup after failure | covered | autouse `reset_agent_task_store_for_tests` |
| 31 | Durable rows removed for namespace | partial | process store only; Postgres table not asserted empty |
| 32 | Tests pass individually | covered | suite design (no cross-test fixtures) |
| 33 | Tests pass repeatedly | covered | store reset; deterministic planner |
| 34 | Order independence | covered | unique IDs; no shared global rows |

### PostgreSQL

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 35 | Concurrent duplicate → one logical op | gap | needs live Postgres + concurrent workers |
| 36 | Concurrent tests remain isolated | partial | process-store isolation only |
| 37 | Migration-backed checkpoint persistence | partial | `test_migration_file_exists` (file presence, not applied DB) |

### Intentionally deferred (scope)

- Live LLM planner
- MCP / external SaaS
- Full JobWorker process-kill harness
- Production Postgres apply in CI without credentials

Next coverage priorities: #17, #28, #23/#24 with a real SCRIPT/HTTP operation under AGENT, #35 Postgres concurrency.

