# Authentication, Tenancy & Isolation

## AUTH_MODE

| Mode | Behavior |
|------|----------|
| `dual` (default) | Accept local HS256 JWT **or** Supabase access token |
| `local` | Local username/password JWT only |
| `supabase` | Supabase JWT only (hosted production) |

Set in `.env`:

```bash
AUTH_MODE=supabase   # production with Supabase
AUTH_MODE=dual       # migration / mixed
AUTH_MODE=local      # offline / VPS without Supabase
```

## Supabase + Google OAuth

1. Configure Supabase project URL + anon key (`SUPABASE_URL`, `SUPABASE_KEY`).
2. Enable Google provider in the Supabase dashboard.
3. Frontend uses the Supabase JS client; backend verifies the JWT and maps
   `auth.uid()` → `User.supabase_id` → `User.id`.
4. On first sign-in, DevOS creates the local `User`, personal `Tenant`, and
   `Membership`, and sets `User.default_tenant_id`.

Service-role keys must **never** ship to the browser. Backend routes still
filter by `user.id` / tenant membership even if the server uses a service role.

## Isolation model

```
Supabase auth.uid()  →  User.supabase_id  →  User.id
                                           →  default_tenant_id
                                           →  resource.owner_id / user_id / tenant_id
```

- Client-supplied `user_id`, `tenant_id`, `owner_id`, `trust_level`, `extra_caps`
  are **not** authoritative.
- Settings PUT strips governance/authority fields.
- Secrets list/get never return decrypted values or ciphertext.

## System vs user provider config

| Layer | Source | Visible to user |
|-------|--------|-----------------|
| System providers | Server `.env` / admin config | configured: true/false |
| User preferences | `UserSettings.providers` | endpoints, defaults, enabled flags |
| User credentials | `Secret` rows `PROVIDER_<ID>_KEY` | configured: true only |

Raw API keys are never returned in JSON responses.

## Default bootstrap admin (local mode)

`ADMIN_USER` / `ADMIN_PASSWORD` (see `.env.example`) create the first admin when
no admin exists. Change the password before production use.
