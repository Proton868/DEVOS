# Supabase / Postgres — Single Source of Truth

DevOS application domain state is stored in **Supabase (Postgres)** only.

- `REQUIRE_POSTGRES=true` (default) — SQLite application URLs are **rejected at startup**
- `DATABASE_URL` must be a Postgres/Supabase connection string
- Local/test only: `REQUIRE_POSTGRES=false` with an explicit sqlite test URL
- Filesystem holds workspace **artifact bytes** only, not authoritative metadata

See also: `ops/env_validate.py --production`, `docs/PRODUCTION_OPERATOR_CHECKLIST.md`

---
# DevOS Single Source of Truth — Supabase / Postgres

Project: **DEVOS** · ref: `heinpngifdqsykqzufhe`

## Principle

- **Postgres (Supabase)** is the authoritative store for users, agents, missions, tasks, delegations, A2A messages, evidence metadata, Ponytail checks, artifact metadata, and orchestration state.
- **Filesystem** (`data/projects/...`) holds workspace file bytes only; metadata is synced via `artifacts` / `artifact_versions`.
- **SQLite is not an application state store.** Set `REQUIRE_POSTGRES=true` (default).

## Apply migrations

```bash
# Linked Supabase project
supabase link --project-ref heinpngifdqsykqzufhe
supabase db push

# Or apply SQL directly
psql "$DATABASE_URL" -f supabase/migrations/20260915130000_devos_single_source_of_truth.sql
```

## App config

```bash
DATABASE_URL=postgresql+psycopg://postgres.[ref]:[PASSWORD]@aws-0-[region].pooler.supabase.com:6543/postgres
REQUIRE_POSTGRES=true
SUPABASE_URL=https://heinpngifdqsykqzufhe.supabase.co
SUPABASE_KEY=<anon key>   # frontend only — never service role in browser
```

Backend uses `DATABASE_URL` (server-side). Frontend uses anon key only.

## Repositories

`core/repositories/agency.py` — missions, tasks, delegations, work history, artifact metadata, Ponytail checks.
