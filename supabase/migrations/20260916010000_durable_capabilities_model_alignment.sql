-- Align durable_capabilities with the SQLAlchemy DurableCapability model.
-- Supabase/Postgres migrations are the production schema authority.
-- Existing definition data is preserved.

ALTER TABLE durable_capabilities
    ADD COLUMN IF NOT EXISTS owner_id TEXT,
    ADD COLUMN IF NOT EXISTS version TEXT NOT NULL DEFAULT '1.0.0',
    ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'system',
    ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS risk TEXT NOT NULL DEFAULT 'medium',
    ADD COLUMN IF NOT EXISTS body JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS signature TEXT,
    ADD COLUMN IF NOT EXISTS approval_state TEXT NOT NULL DEFAULT 'approved',
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_durable_capabilities_tenant
    ON durable_capabilities(tenant_id);

CREATE INDEX IF NOT EXISTS idx_durable_capabilities_owner
    ON durable_capabilities(owner_id);

CREATE INDEX IF NOT EXISTS idx_durable_capabilities_slug
    ON durable_capabilities(slug);

CREATE INDEX IF NOT EXISTS idx_durable_capabilities_active_approval
    ON durable_capabilities(is_active, approval_state);

-- Keep the existing definition column intact. The application currently
-- persists the richer capability representation in body.
