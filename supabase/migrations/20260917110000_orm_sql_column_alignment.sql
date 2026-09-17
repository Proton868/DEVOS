-- Align Postgres tables with SQLAlchemy models (owner_id spine).
ALTER TABLE public.workflow_records ADD COLUMN IF NOT EXISTS owner_id TEXT;
UPDATE public.workflow_records SET owner_id = user_id WHERE owner_id IS NULL AND user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_workflow_records_owner ON public.workflow_records (owner_id);
ALTER TABLE public.evidence_records ADD COLUMN IF NOT EXISTS owner_id TEXT;
CREATE INDEX IF NOT EXISTS idx_evidence_records_owner ON public.evidence_records (owner_id);
ALTER TABLE public.durable_capabilities ADD COLUMN IF NOT EXISTS owner_id TEXT;
CREATE INDEX IF NOT EXISTS idx_durable_capabilities_owner ON public.durable_capabilities (owner_id);
CREATE TABLE IF NOT EXISTS public.secrets (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    encrypted_value TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_secrets_owner ON public.secrets (owner_id);
CREATE TABLE IF NOT EXISTS public.scripts (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    name TEXT,
    description TEXT,
    content TEXT,
    language TEXT,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_scripts_owner ON public.scripts (owner_id);
CREATE TABLE IF NOT EXISTS public.user_settings (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE REFERENCES public.users(id) ON DELETE CASCADE,
    settings JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.custom_endpoints (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    name TEXT,
    base_url TEXT,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_custom_endpoints_user ON public.custom_endpoints (user_id);
