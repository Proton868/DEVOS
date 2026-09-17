# Hardening

- UCI authorize_capability_slug
- AgentIdentity.create no elevation
- Provider secrets masked; admin for PUT
- Chat/script ownership checks
- execution/isolation.py network-off paths
- Tenant/Membership models
- Supabase RLS SQL + apply script


## Current freeze

Governance v1 and Reliability architecture v1 are frozen. Prefer README.md, docs/CURRENT_STATUS.md, and docs/STAGING_DRILLS.md over older audit prose where they conflict.

## Auth surface hardening

- `GET /api/auth/public-config` is browser-visible JSON only (anon key, never service_role).
- SPA catch-all must return JSON 404 for `/api/*`, not React HTML.
- Prefer Supabase Auth for interactive login; DevOS remains the authorization authority.
- See [AUTH_AND_ISOLATION.md](AUTH_AND_ISOLATION.md).
