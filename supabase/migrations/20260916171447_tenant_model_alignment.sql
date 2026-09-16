-- Align public.tenants with the SQLAlchemy Tenant model (tier, is_active, metadata).
-- Safe to re-apply: IF NOT EXISTS preserves existing data and already-aligned schemas.
-- Does not modify RLS, owner_user_id, settings, or other historical columns.

ALTER TABLE public.tenants
    ADD COLUMN IF NOT EXISTS tier VARCHAR(32) NOT NULL DEFAULT 'tenant_user',
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
