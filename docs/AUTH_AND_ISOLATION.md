# Authentication and isolation

Aligned with codebase tip including Supabase-primary login and dual-mode JWT acceptance.

## Responsibilities

| Concern | Owner |
|---------|--------|
| Human authentication | **Supabase Auth** (email/password; optional OAuth/phone) |
| API session credential | DevOS JWT and/or verified Supabase access token |
| Authorization | DevOS (roles, tenants, ownership, UCIP) |
| Capability grants | UCIP / CapabilityRegistry — never model output |

Supabase authenticates **who**. DevOS authorizes **what**.

## Modes (`AUTH_MODE`)

| Value | Behavior |
|-------|----------|
| `dual` (default) | Accept local DevOS JWT **or** verified Supabase access token |
| `supabase` | Supabase tokens only (local `/api/auth/login` JWT rejected) |
| `local` | Local JWT only (Supabase tokens ignored) |

## User-facing login

1. SPA loads Supabase client from `REACT_APP_SUPABASE_*` **or** `GET /api/auth/public-config`.
2. `signInWithPassword` / OAuth / phone via official Supabase JS client.
3. `POST /api/auth/supabase/sync` with `Authorization: Bearer <supabase_access_token>`.
4. Server verifies JWT (JWKS RS256/ES256, or legacy `SUPABASE_JWT_SECRET` HS256).
5. `sync_supabase_user`: link by `User.supabase_id` → email → create Supabase-only user.
6. Server issues DevOS JWT + `devos_token` cookie; SPA stores token for API calls.

Local username/password (`POST /api/auth/login`) is available only when `AUTH_MODE` is `dual` or `local`, via an explicit UI control—not silent fallback that hides Supabase errors.

## Public config (browser-safe)

`GET /api/auth/public-config` returns JSON only:

- `auth_mode`, `auth_enabled`, `supabase_configured`
- `supabase_url`, `supabase_anon_key` (publishable)
- `local_login_available`

**Never** returns `SUPABASE_KEY`, service_role, JWT signing secrets, or database passwords.

SPA catch-all **must not** serve `index.html` for `/api/*` (see `app.py`).

## Environment

| Variable | Exposure |
|----------|----------|
| `SUPABASE_URL` | Server; also in public-config when anon present |
| `SUPABASE_ANON_KEY` | Server public-config / frontend build env |
| `SUPABASE_KEY` | Server only |
| `SUPABASE_JWT_SECRET` | Server only (legacy HS256) |
| `JWT_SECRET` | Server only (DevOS-issued tokens) |

Aliases resolved into `SUPABASE_ANON_KEY` when empty: `SUPABASE_PUBLISHABLE_KEY`, `REACT_APP_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_PUBLIC_KEY`. **`SUPABASE_KEY` is never promoted to anon.**

## Isolation invariants

- Never trust browser-supplied `user_id`, `tenant_id`, or `role` for authorization.
- Never use raw `user_metadata` as an authorization source.
- Resource APIs enforce ownership (task, evidence chain, crawl, graph entity, etc.) — cross-user → 404/403.
- UCIP evaluates capabilities before consequential side effects.
- Tenant context comes from server-side membership / personal tenant helpers.

## Related code

- `api/routes/auth.py` — login, sync, exchange, public-config, `get_current_user`
- `frontend-src/src/services/supabase.js` — client + `ensureSupabase()`
- `frontend-src/src/components/auth/LoginScreen.jsx` — UI
- `core/config.py` — `AUTH_MODE`, Supabase settings
- `tests/test_auth_mode.py`, `tests/test_supabase_auth_login.py`


## Mandatory isolation model (2026-09-17)

| Trust level | Meaning | Required isolation strength |
|-------------|---------|----------------------------|
| **trusted** | Local human / developer IDE terminal | May use host when explicitly intended |
| **untrusted** | AI-generated, uploaded, project, bootstrap, check, coding | **strong** or **restricted** only |
| **privileged** | Host/system/deploy high-risk | **strong** or **restricted** only |

- `network_only` (unshare --net) and `degraded` host **fail closed** for untrusted and privileged.
- No silent fallback to bare host for agent/project commands.
- Canonical path: `run_command_in_project` → `run_governed` → `run_isolated`.
- Project sources cannot spoof `policy=trusted` (classify forces untrusted).
- Refusal contract: `status=isolation_unavailable`, `ok=false`, structured `isolation_evidence`.

Backends (preference): Docker (`DEVOS_USE_DOCKER_SANDBOX=1`) → bubblewrap → firejail → unshare (network_only only) → none.

Human `TerminalService` remains trusted-local shell with env scrub; it is not the agent command path.

