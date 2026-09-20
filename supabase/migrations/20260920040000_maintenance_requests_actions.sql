-- Governed Project Maintain — durable request/action records
-- Postgres SoT; workspace JSON mirrors used for agentic fixture tests.

CREATE TABLE IF NOT EXISTS public.maintenance_requests (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    source_observation_id TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    failure_reason TEXT,
    blocked_reason TEXT,
    unknown_reason TEXT,
    resolution_observation_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT maintenance_requests_status_check CHECK (
        status IN (
            'DETECTED', 'OPEN', 'EVALUATING', 'PLANNED',
            'AWAITING_AUTHORIZATION', 'AUTHORIZED', 'EXECUTING', 'VERIFYING',
            'RESOLVED', 'REJECTED', 'BLOCKED', 'FAILED', 'CANCELLED', 'UNKNOWN'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_maintenance_requests_owner_project
    ON public.maintenance_requests (owner_id, project_id);
CREATE INDEX IF NOT EXISTS idx_maintenance_requests_observation
    ON public.maintenance_requests (source_observation_id);
CREATE INDEX IF NOT EXISTS idx_maintenance_requests_status
    ON public.maintenance_requests (status);

CREATE TABLE IF NOT EXISTS public.maintenance_actions (
    id TEXT PRIMARY KEY,
    maintenance_request_id TEXT NOT NULL
        REFERENCES public.maintenance_requests(id) ON DELETE CASCADE,
    action_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    required BOOLEAN NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    status TEXT NOT NULL,
    operation_id TEXT,
    verification_capability_id TEXT,
    verification_operation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    failure_reason TEXT,
    blocked_reason TEXT,
    unknown_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT maintenance_actions_status_check CHECK (
        status IN (
            'PENDING', 'AUTHORIZED', 'EXECUTING', 'VERIFYING',
            'SUCCEEDED', 'FAILED', 'BLOCKED', 'CANCELLED', 'SKIPPED', 'UNKNOWN'
        )
    ),
    CONSTRAINT maintenance_actions_required_bool CHECK (required IN (true, false))
);

CREATE INDEX IF NOT EXISTS idx_maintenance_actions_request
    ON public.maintenance_actions (maintenance_request_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_maintenance_actions_request_action
    ON public.maintenance_actions (maintenance_request_id, action_id);
