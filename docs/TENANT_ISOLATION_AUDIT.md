# Tenant isolation audit — status

Classification: **B+** — app-layer personal isolation; RLS migrations completed for
notes/documents/layouts; **production schema still unverified**.

Migrations (apply on staging/production):

- `20260917110000_orm_sql_column_alignment.sql`
- `20260917120000_tenant_isolation_rls_completion.sql`
- `20260917180000_notes_documents_layouts_rls.sql`

Org multi-tenancy: **partial** (membership helpers + enterprise tenant_id gate).
See `docs/SAAS_READINESS.md`.

Service role to browser: **CONFIRMED SAFE** (public-config design).

Production-safe multi-tenant SaaS overall: **not certified** until live `pg_policies` verification.
