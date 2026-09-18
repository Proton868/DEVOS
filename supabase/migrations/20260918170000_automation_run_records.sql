-- Durable automation run projection (ExecutionOperation remains execution authority).
CREATE TABLE IF NOT EXISTS public.automation_run_records (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workflow_version INTEGER NOT NULL DEFAULT 1,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    mode TEXT NOT NULL DEFAULT 'manual',
    trigger_type TEXT NOT NULL DEFAULT 'manual',
    trigger_identity TEXT NULL,
    idempotency_key TEXT NULL,
    operation_id TEXT NULL,
    job_id TEXT NULL,
    correlation_id TEXT NULL,
    definition_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_summary JSONB NULL,
    error TEXT NULL,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_automation_run_workflow ON public.automation_run_records (workflow_id);
CREATE INDEX IF NOT EXISTS idx_automation_run_owner ON public.automation_run_records (owner_id);
CREATE INDEX IF NOT EXISTS idx_automation_run_status ON public.automation_run_records (status);
CREATE INDEX IF NOT EXISTS idx_automation_run_idempotency ON public.automation_run_records (owner_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_automation_run_operation ON public.automation_run_records (operation_id)
    WHERE operation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_automation_run_job ON public.automation_run_records (job_id)
    WHERE job_id IS NOT NULL;
