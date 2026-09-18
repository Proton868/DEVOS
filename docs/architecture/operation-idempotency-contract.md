# Consequential operation idempotency contract

## Identity

An **idempotency key identifies the logical consequential operation**, not merely a transport/request attempt.

Database uniqueness for `ExecutionOperation` (when `idempotency_key IS NOT NULL`):

| Component | Role |
|-----------|------|
| `owner_id` | Principal who owns the work |
| `operation_type` | Kind of consequential work (e.g. `tool`, `job:script`) |
| `idempotency_key` | Caller-supplied logical identity |
| `COALESCE(tenant_id, '')` | Tenant scope |

Partial unique index: `ux_execution_operations_idempotency`  
(see migration `20260918160000_execution_operations_idempotency_unique.sql`).

`ExecutionJob` has a separate, tenant-scoped key used at enqueue time; consequential jobs bind to an `ExecutionOperation` in the same transaction.

## Caller rules

1. **Same logical operation → same scoped key**  
   Reusing the key intentionally converges on the existing operation (and job when applicable). Concurrent callers race safely under the unique constraint.

2. **Different logical operation → new key**  
   If arguments, targets, or capability intent represent a *different* consequential action, the caller **must** generate a different key.  
   **Argument payloads are not part of the database uniqueness identity.**  
   `input_digest` / digests may be stored for evidence and forensics; they do **not** split uniqueness.

3. **Tenant and owner remain part of identity**  
   The same string key under different `tenant_id` or `owner_id` yields independent operations.

4. **Absent / NULL key → non-idempotent**  
   When `idempotency_key` is omitted, each reservation creates a new operation. Prefer explicit keys for all side-effecting work.

## Recommended key construction

Prefer `governance.reliability.new_idempotency_key`:

```text
SHA-256( canonical JSON of {
  tenant_id, actor_id, capability, operation, body
} )
```

Include in `body` only the fields that define *what* is being done (stable identifiers, target refs, capability inputs)—not timestamps, request IDs, or noise that would defeat convergence.

`execution_pipeline.begin_execution_job` auto-builds a key for durable paths when the caller does not supply one (payload key names only—callers that need argument-sensitive identity should pass an explicit key).

## What reuse means

| Call pattern | Result |
|--------------|--------|
| Same owner + type + tenant + key | Same `ExecutionOperation.id` |
| Same key, different arguments | **Still the same operation** (by design). Caller must not assume args are re-validated as a uniqueness axis. |
| Different tenant or owner, same key string | Independent operations |
| Key already terminal (`succeeded` / `failed` / `unknown` / `cancelled`) | Reservation still returns that operation identity; lifecycle transitions remain fail-closed |

## Related guarantees

- Concurrent same-key reserve: PostgreSQL unique index + IntegrityError reselect (`tests/test_postgres_execution_concurrency.py`).
- Job + operation create: single transaction (`workers.job_queue.enqueue`).
- UNKNOWN outcomes: never auto-retried; not the same problem as key construction.
