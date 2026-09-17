# Supabase / Postgres — Single Source of Truth

DevOS **application domain state** is stored in **Supabase (Postgres)** only.

## Principle

- `REQUIRE_POSTGRES=true` (production default) — SQLite application URLs are **rejected at startup**
- `DATABASE_URL` must be a Postgres/Supabase connection string
- Tests may use SQLite only when isolation sets `REQUIRE_POSTGRES=false`
- Filesystem holds workspace **artifact bytes** only; metadata belongs in Postgres

## Schema authority

- Owned by `supabase/migrations/*.sql`
- Applied via `ops/apply_migrations.sh` → `scripts/apply_supabase_migrations.py`
- Production Postgres must **not** run SQLAlchemy `Base.metadata.create_all()` (sync engine is SQLite-only for create_all)

## App config

```bash
DATABASE_URL=postgresql+psycopg://...
REQUIRE_POSTGRES=true
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<publishable anon key>   # browser / public-config
SUPABASE_KEY=<server-side key>             # never ship to browser
# optional legacy:
# SUPABASE_JWT_SECRET=<hs256 jwt secret>
AUTH_MODE=dual
```

Backend uses `DATABASE_URL` for ORM. Auth verification uses JWKS (or `SUPABASE_JWT_SECRET`). Frontend uses **anon key only**.

## Related

- `docs/AUTH_AND_ISOLATION.md` — login and token modes
- `docs/SUPABASE_RLS.md` — RLS notes
- `ops/env_validate.py --production`
- `docs/PRODUCTION_OPERATOR_CHECKLIST.md`
