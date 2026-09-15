-- DevOS Single Source of Truth schema (Postgres / Supabase)
-- Project: DEVOS (heinpngifdqsykqzufhe)
-- Preserves existing domain table names used by SQLAlchemy models.
-- New Agency OS tables: agents, souls, work history, A2A, Ponytail, artifacts.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- Core identity / tenancy (application users — linked to auth.users via supabase_id)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    hashed_password TEXT,
    supabase_id TEXT UNIQUE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    role TEXT NOT NULL DEFAULT 'member',
    plan TEXT NOT NULL DEFAULT 'recruit',
    onboarding_status TEXT NOT NULL DEFAULT 'NOT_STARTED',
    display_name TEXT,
    preferred_name TEXT,
    avatar_url TEXT,
    bio TEXT,
    job_title TEXT,
    organization TEXT,
    timezone TEXT,
    default_tenant_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    settings JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_users_supabase_id ON users(supabase_id);

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE,
    owner_user_id TEXT,
    settings JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memberships (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);

-- ---------------------------------------------------------------------------
-- Existing domain tables (SQLAlchemy-compatible)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT,
    provider TEXT,
    model TEXT,
    persona_id TEXT,
    system_prompt TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS persona_profiles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    persona_id TEXT NOT NULL,
    display_name TEXT,
    description TEXT,
    provider TEXT,
    model TEXT,
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    tasks_successful INTEGER NOT NULL DEFAULT 0,
    tasks_failed INTEGER NOT NULL DEFAULT 0,
    verified_outcomes INTEGER NOT NULL DEFAULT 0,
    delegations_received INTEGER NOT NULL DEFAULT 0,
    delegations_successful INTEGER NOT NULL DEFAULT 0,
    specialty_xp JSONB DEFAULT '{}'::jsonb,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, persona_id)
);

CREATE TABLE IF NOT EXISTS persona_experience_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    persona_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    mission_id TEXT,
    task_id TEXT,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orchestration_plans (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    goal TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'plan',
    status TEXT NOT NULL DEFAULT 'idle',
    plan_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_orch_plans_user ON orchestration_plans(user_id);
CREATE INDEX IF NOT EXISTS idx_orch_plans_status ON orchestration_plans(status);

CREATE TABLE IF NOT EXISTS workflow_records (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT,
    name TEXT,
    definition JSONB DEFAULT '{}'::jsonb,
    status TEXT,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence_records (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    owner_id TEXT,
    kind TEXT,
    payload JSONB DEFAULT '{}'::jsonb,
    correlation_id TEXT,
    operation_id TEXT,
    job_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_evidence_corr ON evidence_records(correlation_id);

CREATE TABLE IF NOT EXISTS execution_jobs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    owner_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    payload JSONB DEFAULT '{}'::jsonb,
    result JSONB,
    operation_id TEXT,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution_operations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    owner_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved',
    idempotency_key TEXT,
    execution_job_id TEXT,
    evidence_id TEXT,
    payload JSONB DEFAULT '{}'::jsonb,
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT,
    project_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'agent',
    status TEXT NOT NULL DEFAULT 'queued',
    current_tool TEXT,
    files_changed JSONB DEFAULT '[]'::jsonb,
    tools_used JSONB DEFAULT '[]'::jsonb,
    correlation_id TEXT,
    error TEXT,
    summary TEXT,
    provider TEXT,
    model TEXT,
    events JSONB DEFAULT '[]'::jsonb,
    hai_checkpoint JSONB,
    recovery_owner TEXT,
    recovery_lease_expires_at TIMESTAMPTZ,
    -- provenance
    actor_type TEXT,
    actor_id TEXT,
    delegated_by_type TEXT,
    delegated_by_id TEXT,
    mission_id TEXT,
    agent_id TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_user ON agent_tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_mission ON agent_tasks(mission_id);

CREATE TABLE IF NOT EXISTS durable_capabilities (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    slug TEXT NOT NULL,
    definition JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS worker_trust_records (
    id TEXT PRIMARY KEY,
    worker_id TEXT,
    tenant_id TEXT,
    trust_level TEXT,
    competency JSONB,
    pending_promotion JSONB,
    promotion_expires_at TIMESTAMPTZ,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Agency OS: agents, personas, souls, work history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'persona', -- persona | catalog | system
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    default_capabilities JSONB DEFAULT '[]'::jsonb,
    default_tools JSONB DEFAULT '[]'::jsonb,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_personas (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    persona_key TEXT NOT NULL, -- nuha, web, code, ...
    role TEXT NOT NULL DEFAULT 'specialist', -- orchestrator | specialist
    can_delegate BOOLEAN NOT NULL DEFAULT FALSE,
    system_prompt TEXT,
    agent_slug TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (persona_key)
);

CREATE TABLE IF NOT EXISTS agent_profiles (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id TEXT,
    display_name TEXT,
    provider TEXT,
    model TEXT,
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    stats JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_id, user_id)
);

CREATE TABLE IF NOT EXISTS agent_souls (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    principles JSONB DEFAULT '[]'::jsonb,
    preferences JSONB DEFAULT '{}'::jsonb,
    lessons JSONB DEFAULT '[]'::jsonb,
    memory_refs JSONB DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_id, user_id)
);

CREATE TABLE IF NOT EXISTS agent_work_history (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    tenant_id TEXT,
    -- provenance
    actor_type TEXT NOT NULL, -- human | nuha | agent
    actor_id TEXT NOT NULL,
    delegated_by_type TEXT,
    delegated_by_id TEXT,
    mission_id TEXT,
    task_id TEXT,
    -- work
    action TEXT NOT NULL,
    tools_used JSONB DEFAULT '[]'::jsonb,
    files_changed JSONB DEFAULT '[]'::jsonb,
    outcome TEXT NOT NULL DEFAULT 'unknown',
    evidence_id TEXT,
    ponytail_check_id TEXT,
    summary TEXT,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_work_hist_agent ON agent_work_history(agent_id);
CREATE INDEX IF NOT EXISTS idx_work_hist_mission ON agent_work_history(mission_id);
CREATE INDEX IF NOT EXISTS idx_work_hist_user ON agent_work_history(user_id);

-- Missions / tasks / delegations (normalized; orchestration_plans remains snapshot)
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id TEXT,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    plan_id TEXT, -- optional FK-ish to orchestration_plans.id
    actor_type TEXT DEFAULT 'nuha',
    actor_id TEXT,
    outcome TEXT,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_missions_user ON missions(user_id);
CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status);

CREATE TABLE IF NOT EXISTS mission_tasks (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    agent_id TEXT,
    persona_key TEXT,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    sequence_no INTEGER NOT NULL DEFAULT 0,
    agent_task_id TEXT, -- link to agent_tasks when executed
    outcome TEXT,
    evidence_id TEXT,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mission_tasks_mission ON mission_tasks(mission_id);

CREATE TABLE IF NOT EXISTS task_delegations (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    from_actor_type TEXT NOT NULL,
    from_actor_id TEXT NOT NULL,
    to_actor_type TEXT NOT NULL,
    to_actor_id TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_delegations_mission ON task_delegations(mission_id);

-- A2A messages / events
CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    mission_id TEXT,
    task_id TEXT,
    from_agent_id TEXT NOT NULL,
    to_agent_id TEXT NOT NULL,
    envelope JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agent_msgs_to ON agent_messages(to_agent_id);

CREATE TABLE IF NOT EXISTS agent_events (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    mission_id TEXT,
    task_id TEXT,
    event_type TEXT NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb,
    actor_type TEXT,
    actor_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tool_executions (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    user_id TEXT,
    tenant_id TEXT,
    mission_id TEXT,
    task_id TEXT,
    tool_name TEXT NOT NULL,
    args_digest TEXT,
    status TEXT NOT NULL DEFAULT 'started',
    outcome TEXT,
    evidence_id TEXT,
    operation_id TEXT,
    actor_type TEXT,
    actor_id TEXT,
    delegated_by_type TEXT,
    delegated_by_id TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tool_exec_task ON tool_executions(task_id);

-- Ponytail acceptance checks
CREATE TABLE IF NOT EXISTS ponytail_checks (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    user_id TEXT NOT NULL,
    mission_id TEXT,
    task_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending', -- pending|passed|failed|skipped
    stage_results JSONB DEFAULT '[]'::jsonb,
    self_check TEXT,
    summary TEXT,
    evidence_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

-- Artifacts metadata (filesystem is blob surface; this is SoT for metadata)
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT,
    project_id TEXT NOT NULL DEFAULT 'default',
    path TEXT NOT NULL,
    kind TEXT DEFAULT 'file',
    mission_id TEXT,
    task_id TEXT,
    agent_id TEXT,
    content_hash TEXT,
    size_bytes BIGINT,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, project_id, path)
);

CREATE TABLE IF NOT EXISTS artifact_versions (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content_hash TEXT,
    size_bytes BIGINT,
    actor_type TEXT,
    actor_id TEXT,
    mission_id TEXT,
    task_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, version)
);

-- ---------------------------------------------------------------------------
-- RLS: enable on exposed tables; ownership via app user_id / membership
-- Backend service role bypasses RLS; anon/authenticated must not.
-- Authorization uses table columns + auth.uid() ↔ users.supabase_id linkage.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'users','tenants','memberships','chat_sessions','messages',
    'persona_profiles','persona_experience_events','orchestration_plans',
    'workflow_records','evidence_records','execution_jobs','execution_operations',
    'agent_tasks','agents','agent_personas','agent_profiles','agent_souls',
    'agent_work_history','missions','mission_tasks','task_delegations',
    'agent_messages','agent_events','tool_executions','ponytail_checks',
    'artifacts','artifact_versions','durable_capabilities','worker_trust_records'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- Helper: resolve app user id from auth.uid() without SECURITY DEFINER in public
-- Policies use: EXISTS (SELECT 1 FROM users u WHERE u.supabase_id = auth.uid()::text AND u.id = <row>.user_id)

-- users: own row only
DROP POLICY IF EXISTS users_select_own ON users;
CREATE POLICY users_select_own ON users FOR SELECT
  USING (supabase_id = auth.uid()::text OR id = auth.uid()::text);

DROP POLICY IF EXISTS users_update_own ON users;
CREATE POLICY users_update_own ON users FOR UPDATE
  USING (supabase_id = auth.uid()::text);

-- missions
DROP POLICY IF EXISTS missions_owner_all ON missions;
CREATE POLICY missions_owner_all ON missions FOR ALL
  USING (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  )
  WITH CHECK (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  );

-- agent_work_history
DROP POLICY IF EXISTS work_hist_owner_select ON agent_work_history;
CREATE POLICY work_hist_owner_select ON agent_work_history FOR SELECT
  USING (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  );

DROP POLICY IF EXISTS work_hist_owner_insert ON agent_work_history;
CREATE POLICY work_hist_owner_insert ON agent_work_history FOR INSERT
  WITH CHECK (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  );

-- agent_tasks
DROP POLICY IF EXISTS agent_tasks_owner ON agent_tasks;
CREATE POLICY agent_tasks_owner ON agent_tasks FOR ALL
  USING (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  )
  WITH CHECK (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  );

-- orchestration_plans
DROP POLICY IF EXISTS orch_owner ON orchestration_plans;
CREATE POLICY orch_owner ON orchestration_plans FOR ALL
  USING (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  )
  WITH CHECK (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  );

-- artifacts
DROP POLICY IF EXISTS artifacts_owner ON artifacts;
CREATE POLICY artifacts_owner ON artifacts FOR ALL
  USING (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  )
  WITH CHECK (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  );

-- agent_profiles / souls
DROP POLICY IF EXISTS agent_profiles_owner ON agent_profiles;
CREATE POLICY agent_profiles_owner ON agent_profiles FOR ALL
  USING (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  )
  WITH CHECK (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  );

DROP POLICY IF EXISTS agent_souls_owner ON agent_souls;
CREATE POLICY agent_souls_owner ON agent_souls FOR ALL
  USING (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  )
  WITH CHECK (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  );

-- memberships: member can read own memberships
DROP POLICY IF EXISTS memberships_own ON memberships;
CREATE POLICY memberships_own ON memberships FOR SELECT
  USING (
    user_id IN (SELECT id FROM users WHERE supabase_id = auth.uid()::text)
  );

-- Deny-by-default: no broad "TO authenticated USING (true)" policies.
