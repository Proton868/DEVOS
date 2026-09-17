-- Align public.execution_operations with SQLAlchemy ExecutionOperation (Stage 3M).
-- Base migration 20260915130000 created a minimal table without actor_id and
-- related columns. Live INSERT failed with UndefinedColumn: actor_id even after
-- schema_migrations listed that file as applied (CREATE TABLE IF NOT EXISTS
-- does not ALTER existing tables).
--
-- ORM (core/database.py ExecutionOperation) relevant columns:
--   actor_id, task_id, tool_name, request_id, correlation_id,
--   input_digest, target_digest, result_digest, attempt,
--   started_at, completed_at, meta
-- All ADD COLUMN IF NOT EXISTS, nullable where Optional, safe on populated DBs.

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS actor_id TEXT NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS task_id TEXT NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS tool_name TEXT NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS request_id TEXT NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS correlation_id TEXT NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS input_digest TEXT NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS target_digest TEXT NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS result_digest TEXT NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS attempt INTEGER NULL DEFAULT 1;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL;

ALTER TABLE public.execution_operations
    ADD COLUMN IF NOT EXISTS meta JSONB NULL;

CREATE INDEX IF NOT EXISTS idx_execution_operations_actor_id
    ON public.execution_operations (actor_id);

CREATE INDEX IF NOT EXISTS idx_execution_operations_task_id
    ON public.execution_operations (task_id);

CREATE INDEX IF NOT EXISTS idx_execution_operations_request_id
    ON public.execution_operations (request_id);
