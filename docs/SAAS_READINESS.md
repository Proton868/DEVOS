# SaaS / multi-tenant readiness (honest status)

**Last updated:** 2026-09-17  
**Verdict:** DevOS does **not** claim production-certified multi-org SaaS isolation
until production RLS is verified and org features are productized.

## What is true today

| Claim | Status |
|-------|--------|
| Personal multi-user isolation (API) | Designed; owner filters on critical routes |
| Notes / documents / workspace_layouts RLS (repo) | Migration `20260917180000_notes_documents_layouts_rls.sql` |
| Service role never in browser public-config | Confirmed design |
| Org/agency multi-tenancy | **Partial**: personal tenant + memberships; enterprise `tenant_id` now membership-gated |
| Production RLS applied | **UNVERIFIED** until operators run `pg_policies` on live DB |

## What is not claimed

- Full agency/organization multi-tenant SaaS with mature RBAC product UX
- Production-safe multi-tenant isolation without applying migrations and verifying policies

## Operator verification

```bash
# After deploy + apply_migrations
psql "$DATABASE_URL" -c "SELECT tablename, policyname FROM pg_policies WHERE schemaname='public' ORDER BY 1,2;"
# Expect notes_owner, documents_owner, workspace_layouts_owner, secrets_owner, memories_owner_*, ...
```

## API

- `GET /api/enterprise/tenants/mine` — memberships for the current user
- Enterprise billing/audit/rbac with `tenant_id` require membership (403 otherwise)
- `GET|POST|DELETE /api/notes` — personal notes only
