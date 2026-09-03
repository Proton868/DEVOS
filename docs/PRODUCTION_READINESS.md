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
