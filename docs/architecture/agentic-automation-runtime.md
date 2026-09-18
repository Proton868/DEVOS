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

**Forward:** additive table + owner/tenant indexes + partial unique on `(owner_id, idempotency_key)`.

**Rollback:** `20260918200000_agentic_runtime_checkpoints.down.sql` drops indexes/table when unused.

**Compatibility:** additive; old app ignores table; new app uses process store + optional table.

## Security

- Allowlist fixed at delegation
- No self-authorization
- Secrets scrubbed from context
- Fabricated evidence ignored
- Cross-owner/tenant denied via `assert_owner`
