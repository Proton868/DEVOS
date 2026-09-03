# Production readiness notes

## Classification guidance

DevOS is intended for **single-node** deployment with:

- Backend-owned application data (SQLite or Postgres via `DATABASE_URL`)
- Supabase for authentication (recommended `AUTH_MODE=supabase`)
- Workflow definitions persisted in application database (multi-instance ready for definitions)

## Required configuration

| Variable | Notes |
|----------|--------|
| `DEBUG=false` | Required for production |
| `JWT_SECRET` | Explicit long random string |
| `ADMIN_PASSWORD` | Strong unique value **or empty** to auto-generate; defaults like `123456..` are **rejected** when `DEBUG=false` |
| `ALLOWED_ORIGINS` | Real HTTPS origin(s), not localhost |
| `AUTH_MODE` | `supabase` (or `dual` during migration) |
| `SUPABASE_URL` / verification key | HTTPS URL; never put service-role key in frontend |
| Database | `DATABASE_URL` |

## Explicit technical debt

- **Workflows** definitions are database-backed (`workflow_records`). Runtime may cache; DB is authoritative.
- **Google OAuth** requires real Supabase + Google Cloud configuration; not verified in CI without secrets.
- **Supabase RLS** applies only if tables are exposed via Supabase client; default app data is backend-local.

## Test suite (provisioned environment)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -q
cd frontend-src && npm ci && npm run build
```

## Workflow execution snapshots

- Definitions live in `workflow_records` (DB authoritative).
- `POST /api/workflows/{id}/execute` creates an `ExecutionJob` with
  `workflow_id`, `workflow_version`, and an immutable `payload.workflow_snapshot`.
- Retries/recovery MUST use the job snapshot — never reload the live definition.
- Deleting a workflow removes the definition only; jobs and evidence retain historical IDs/versions.
- Workflow `schedule`/`cron` fields are stored but **not** auto-scheduled (script scheduler only).
- Governance/capability checks remain authoritative at step execution time.

## WorkflowExecutionSnapshot contract (schema_version = 1)

Canonical fields: `schema_version`, `workflow_id`, `workflow_version`, `owner_id`,
`definition` (self-contained), `captured_at`, plus `tenant_id` / `correlation_id` when applicable.

### Guarantees

| Guarantee | Detail |
|-----------|--------|
| Immutability | Editing WorkflowRecord does not mutate existing job snapshots |
| Identity match | `job.workflow_id/version` must equal snapshot fields |
| No live fallback | Corrupt/missing snapshot → failed/invalid; never reload current definition |
| Secrets | `scrub_secrets()` before persist |
| Job states | `queued` → `running` → `succeeded` \| `failed` (existing model) |
| Evidence | Records version from snapshot/job, not current WorkflowRecord |

### Failure matrix (summary)

| Failure | Job? | Execution? | Evidence of success? |
|---------|------|------------|----------------------|
| Not found / not owner | No | No | No |
| Invalid definition | No | No | No |
| Disabled workflow | No | No | No |
| Enqueue success | Yes (`queued`) | Not yet | Acceptance evidence only |
| Step failure | Yes | Partial | FAILED, snapshot retained |
| External side effect then fail | Yes | Yes | FAILED; **no false rollback claim** |

### Limitations

- External side effects are **not** automatically reversible.
- Workflow `schedule` metadata is **not** an active scheduler registration.
- Compensation engines are not implemented.

## Workflow step executor

`brain/workflow_executor.py` runs jobs with `job_type=workflow` from the
immutable `payload.workflow_snapshot` only.

Supported step types:
- `notify` — log/record, no external side effect
- `wait` — bounded sleep (max 30s)
- `condition` — safe expression over context
- `capability` — UCIP + `require_authority`; sandbox only when code is supplied
- `approval` — fail-closed (pending_approval ends run)
- `parallel` — marker (children sequentialized)
- `subflow` — not enabled (fails explicitly)

Registered on `JobWorker` at app startup. Retries use the same snapshot.
