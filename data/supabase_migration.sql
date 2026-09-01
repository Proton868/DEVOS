-- =============================================================================
-- CaraiOS Supabase Multi-Tenant Migration
-- Run this in the Supabase SQL Editor to set up the multi-tenant schema
-- with pgvector, RLS policies, and the full tenant-aware memory table.
-- =============================================================================

-- 1. Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- 2. Tenants table
CREATE TABLE IF NOT EXISTS tenants (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    tier        TEXT NOT NULL DEFAULT 'tenant_user'
                CHECK (tier IN ('public', 'tenant_user', 'tenant_admin', 'agency_operator', 'system')),
    settings    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Tenant memberships (join table)
CREATE TABLE IF NOT EXISTS tenant_memberships (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'member'
                CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_memberships_user ON tenant_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_memberships_tenant ON tenant_memberships(tenant_id);

-- 4. Profiles table (extends auth.users)
CREATE TABLE IF NOT EXISTS profiles (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    username    TEXT,
    full_name   TEXT,
    avatar_url  TEXT,
    tenant_id   UUID REFERENCES tenants(id),
    trust_tier  TEXT DEFAULT 'tenant_user',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Auto-create profile on signup
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO profiles (id, username, full_name, avatar_url)
    VALUES (
        NEW.id,
        NEW.raw_user_meta_data->>'username',
        NEW.raw_user_meta_data->>'full_name',
        NEW.raw_user_meta_data->>'avatar_url'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION handle_new_user();

-- 5. CaraiOS memories table with pgvector
CREATE TABLE IF NOT EXISTS caraios_memories (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL,
    tenant_id   UUID REFERENCES tenants(id),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'episodic'
                CHECK (kind IN ('episodic', 'semantic', 'working', 'long_term', 'tenant', 'learning')),
    session_id  TEXT,
    metadata    JSONB DEFAULT '{}'::jsonb,
    embedding   vector(768),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memories_user ON caraios_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_tenant ON caraios_memories(tenant_id);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON caraios_memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_embedding ON caraios_memories
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 6. Vector similarity search function
CREATE OR REPLACE FUNCTION match_memories(
    query_embedding vector(768),
    match_user_id   UUID,
    match_count     INT DEFAULT 5,
    match_kind      TEXT DEFAULT NULL,
    match_tenant_id UUID DEFAULT NULL
)
RETURNS TABLE (
    id         UUID,
    user_id    UUID,
    tenant_id  UUID,
    role       TEXT,
    content    TEXT,
    kind       TEXT,
    session_id TEXT,
    metadata   JSONB,
    created_at TIMESTAMPTZ,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id, m.user_id, m.tenant_id, m.role, m.content,
        m.kind, m.session_id, m.metadata, m.created_at,
        1 - (m.embedding <=> query_embedding) AS similarity
    FROM caraios_memories m
    WHERE m.user_id = match_user_id
      AND (match_kind IS NULL OR m.kind = match_kind)
      AND (match_tenant_id IS NULL OR m.tenant_id = match_tenant_id)
    ORDER BY m.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- 7. Capability registry table
CREATE TABLE IF NOT EXISTS capability_registry (
    slug            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    category        TEXT NOT NULL DEFAULT 'function',
    risk            TEXT NOT NULL DEFAULT 'low',
    trust_level     TEXT NOT NULL DEFAULT 'tenant_user',
    input_schema    JSONB DEFAULT '{}'::jsonb,
    output_schema   JSONB DEFAULT '{}'::jsonb,
    model_binding   TEXT,
    version         TEXT DEFAULT '1.0.0',
    signature       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 8. Workflow definitions table
CREATE TABLE IF NOT EXISTS workflows (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID REFERENCES tenants(id),
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    version     TEXT DEFAULT '1.0.0',
    steps       JSONB DEFAULT '[]'::jsonb,
    start_step  TEXT,
    triggers    JSONB DEFAULT '["manual"]'::jsonb,
    schedule    TEXT,
    tags        JSONB DEFAULT '[]'::jsonb,
    status      TEXT DEFAULT 'draft',
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workflows_tenant ON workflows(tenant_id);

-- 9. Evidence chain storage
CREATE TABLE IF NOT EXISTS evidence_chains (
    chain_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID REFERENCES tenants(id),
    goal        TEXT DEFAULT '',
    identity_context JSONB DEFAULT '{}'::jsonb,
    nodes       JSONB DEFAULT '{}'::jsonb,
    status      TEXT DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evidence_tenant ON evidence_chains(tenant_id);

-- 10. Research reports
CREATE TABLE IF NOT EXISTS research_reports (
    report_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID REFERENCES tenants(id),
    user_id     UUID NOT NULL,
    question    TEXT NOT NULL,
    summary     TEXT DEFAULT '',
    sources     JSONB DEFAULT '[]'::jsonb,
    citations   JSONB DEFAULT '[]'::jsonb,
    full_report TEXT DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_research_tenant ON research_reports(tenant_id);
CREATE INDEX IF NOT EXISTS idx_research_user ON research_reports(user_id);

-- =============================================================================
-- ROW LEVEL SECURITY
-- =============================================================================

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Tenants are viewable by members"
    ON tenants FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM tenant_memberships tm
            WHERE tm.tenant_id = tenants.id
              AND tm.user_id = auth.uid()
        )
    );

CREATE POLICY "Tenants can be created by authenticated users"
    ON tenants FOR INSERT
    WITH CHECK (auth.role() = 'authenticated');

CREATE POLICY "Tenants can be updated by admins"
    ON tenants FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM tenant_memberships tm
            WHERE tm.tenant_id = tenants.id
              AND tm.user_id = auth.uid()
              AND tm.role IN ('owner', 'admin')
        )
    );

ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Memberships viewable by tenant members"
    ON tenant_memberships FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM tenant_memberships tm2
            WHERE tm2.tenant_id = tenant_memberships.tenant_id
              AND tm2.user_id = auth.uid()
        )
    );

CREATE POLICY "Memberships manageable by tenant admins"
    ON tenant_memberships FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM tenant_memberships tm
            WHERE tm.tenant_id = tenant_memberships.tenant_id
              AND tm.user_id = auth.uid()
              AND tm.role IN ('owner', 'admin')
        )
    );

CREATE POLICY "Memberships deletable by tenant admins"
    ON tenant_memberships FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM tenant_memberships tm
            WHERE tm.tenant_id = tenant_memberships.tenant_id
              AND tm.user_id = auth.uid()
              AND tm.role IN ('owner', 'admin')
        )
    );

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own profile"
    ON profiles FOR SELECT
    USING (id = auth.uid());

CREATE POLICY "Users can update own profile"
    ON profiles FOR UPDATE
    USING (id = auth.uid());

ALTER TABLE caraios_memories ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can access own memories"
    ON caraios_memories FOR ALL
    USING (user_id = auth.uid());

ALTER TABLE capability_registry ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Capabilities readable by all authenticated users"
    ON capability_registry FOR SELECT
    USING (auth.role() = 'authenticated');

ALTER TABLE workflows ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Workflows accessible by tenant members"
    ON workflows FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM tenant_memberships tm
            WHERE tm.tenant_id = workflows.tenant_id
              AND tm.user_id = auth.uid()
        )
    );

ALTER TABLE evidence_chains ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Evidence chains accessible by tenant members"
    ON evidence_chains FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM tenant_memberships tm
            WHERE tm.tenant_id = evidence_chains.tenant_id
              AND tm.user_id = auth.uid()
        )
    );

ALTER TABLE research_reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Research reports accessible by tenant members"
    ON research_reports FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM tenant_memberships tm
            WHERE tm.tenant_id = research_reports.tenant_id
              AND tm.user_id = auth.uid()
        )
    );

-- =============================================================================
-- DEFAULT DATA
-- =============================================================================

INSERT INTO tenants (id, name, slug, tier)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Default',
    'default',
    'agency_operator'
) ON CONFLICT (slug) DO NOTHING;

CREATE OR REPLACE FUNCTION add_default_tenant_membership()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO tenant_memberships (tenant_id, user_id, role)
    VALUES (
        '00000000-0000-0000-0000-000000000001',
        NEW.id,
        'member'
    ) ON CONFLICT (tenant_id, user_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_profile_created ON profiles;
CREATE TRIGGER on_profile_created
    AFTER INSERT ON profiles
    FOR EACH ROW EXECUTE FUNCTION add_default_tenant_membership();