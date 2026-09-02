# Supabase RLS — accurate scope

## What uses Supabase today

| Surface | Storage | Client access | RLS needed? |
|---------|---------|---------------|-------------|
| Auth (login, Google OAuth, sessions) | Supabase Auth | Browser via anon key + JWT | Managed by Supabase Auth |
| Application data (chats, scripts, secrets, settings, jobs, evidence, workflows) | **Local SQLite / optional Postgres via `DATABASE_URL`** | **Backend only** | N/A for direct client access — backend enforces ownership |
| Optional enterprise tables via `scripts/apply_supabase_schema.py` | Supabase Postgres when configured | Prefer backend; if client-direct, enable RLS | Yes before any client-direct SELECT |

## Policy

- DevOS does **not** currently expose application tables to the browser through the Supabase data API by default.
- Authorization for application data is enforced in FastAPI routes (`get_current_user` + `user_id` / `owner_id` / personal tenant filters).
- Service-role keys must never ship to the frontend. Even with service-role on the server, route handlers must still filter by authenticated identity.

## Google OAuth (Dashboard)

1. Supabase → Authentication → Providers → Google: enable, set Client ID/Secret from Google Cloud Console.
2. Authentication → URL configuration:
   - Site URL: your deployed origin (e.g. `https://dev.carai.agency`)
   - Redirect URLs: same origin path(s) the SPA is served from
3. Frontend env: `REACT_APP_SUPABASE_URL`, `REACT_APP_SUPABASE_ANON_KEY`
4. Backend env: `SUPABASE_URL`, `SUPABASE_KEY` (or JWT secret for verification), `AUTH_MODE=supabase` or `dual`

Flow: Google → Supabase session → `onAuthStateChange` / `/api/auth/supabase/sync` → local `User` + personal tenant.

## Applying optional schema

```bash
export SUPABASE_DB_URL=postgresql://...
python3 scripts/apply_supabase_schema.py
```

Only enable client-direct access to those tables after writing RLS policies tied to `auth.uid()` and membership mapping. Until then, keep access backend-only.
