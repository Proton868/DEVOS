-- Rollback for 20260918200000_agentic_runtime_checkpoints
-- Safe only if application no longer reads agentic_runtime_checkpoints.
DROP INDEX IF EXISTS uq_agentic_cp_owner_idem;
DROP INDEX IF EXISTS idx_agentic_cp_parent_run;
DROP INDEX IF EXISTS idx_agentic_cp_state;
DROP INDEX IF EXISTS idx_agentic_cp_tenant;
DROP INDEX IF EXISTS idx_agentic_cp_owner;
DROP TABLE IF EXISTS agentic_runtime_checkpoints;
