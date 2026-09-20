-- Governed Incident domain — durable incident records
-- Independent from maintenance_requests / observations / tasks.

CREATE TABLE IF NOT EXISTS public.incidents (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    source_observation_id TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_version INTEGER NOT NULL DEFAULT 1,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    summary TEXT,
    failure_reason TEXT,
    blocked_reason TEXT,
    unknown_reason TEXT,
    resolution_observation_id TEXT,
    maintenance_request_id TEXT,
    correlation_key TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT incidents_severity_check CHECK (
        severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
    ),
    CONSTRAINT incidents_status_check CHECK (
        status IN (
            'DETECTED', 'OPEN', 'ASSESSING', 'ACKNOWLEDGED', 'MITIGATING',
            'VERIFYING', 'RESOLVED', 'REJECTED', 'CANCELLED', 'DUPLICATE',
            'BLOCKED', 'FAILED', 'UNKNOWN'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_incidents_owner_project
    ON public.incidents (owner_id, project_id);
CREATE INDEX IF NOT EXISTS idx_incidents_deployment
    ON public.incidents (deployment_id);
CREATE INDEX IF NOT EXISTS idx_incidents_status
    ON public.incidents (status);
CREATE INDEX IF NOT EXISTS idx_incidents_correlation
    ON public.incidents (correlation_key);
CREATE INDEX IF NOT EXISTS idx_incidents_source_observation
    ON public.incidents (source_observation_id);
