# Durable Agentic Automation Runtime

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
