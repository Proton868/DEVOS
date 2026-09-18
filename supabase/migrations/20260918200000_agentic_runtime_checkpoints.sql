-- Additive: durable agentic runtime checkpoints (optional SoT beyond process store).
-- Forward: create table + indexes. Safe defaults. No destructive changes.
-- Rollback: DROP TABLE IF EXISTS agentic_runtime_checkpoints; (only if no dependent app requires it)

CREATE TABLE IF NOT EXISTS agentic_runtime_checkpoints (
    task_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT,
    parent_run_id TEXT,
    workflow_version INTEGER,
    agent_type TEXT NOT NULL DEFAULT 'worker',
    state TEXT NOT NULL DEFAULT 'created',
    turn INTEGER NOT NULL DEFAULT 0,
    max_turns INTEGER NOT NULL DEFAULT 8,
    allowed_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    pending_request JSONB,
    last_observation JSONB,
    plan JSONB,
    operation_id TEXT,
    job_id TEXT,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    capability_request_count INTEGER NOT NULL DEFAULT 0,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    unknown_info JSONB,
    failure TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT,
    checkpoint JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agentic_cp_owner ON agentic_runtime_checkpoints (owner_id);
CREATE INDEX IF NOT EXISTS idx_agentic_cp_tenant ON agentic_runtime_checkpoints (tenant_id);
CREATE INDEX IF NOT EXISTS idx_agentic_cp_state ON agentic_runtime_checkpoints (state);
CREATE INDEX IF NOT EXISTS idx_agentic_cp_parent_run ON agentic_runtime_checkpoints (parent_run_id);

-- Idempotent unique: one logical agent task key per owner
CREATE UNIQUE INDEX IF NOT EXISTS uq_agentic_cp_owner_idem
    ON agentic_runtime_checkpoints (owner_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
