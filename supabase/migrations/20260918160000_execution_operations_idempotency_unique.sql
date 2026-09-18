-- Stage 3M.4 — Database-enforced operation idempotency (tenant-scoped).
-- Matches reserve_operation() lookup: owner_id + operation_type + idempotency_key + tenant_id.
-- NULL tenant_id is coalesced so multiple NULL tenants cannot collide independently of owner/key.
-- Partial index: only rows WITH an idempotency_key are constrained (NULL key = non-idempotent).

-- Fail closed if duplicate logical keys already exist (do not silently delete).
DO $$
DECLARE
  dup_count integer;
BEGIN
  SELECT COUNT(*) INTO dup_count FROM (
    SELECT owner_id, operation_type, idempotency_key, COALESCE(tenant_id, '') AS tid
    FROM public.execution_operations
    WHERE idempotency_key IS NOT NULL
    GROUP BY owner_id, operation_type, idempotency_key, COALESCE(tenant_id, '')
    HAVING COUNT(*) > 1
  ) d;
  IF dup_count > 0 THEN
    RAISE EXCEPTION
      'execution_operations idempotency unique index blocked: % duplicate key group(s) exist — reconcile before applying',
      dup_count;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_operations_idempotency
ON public.execution_operations (
  owner_id,
  operation_type,
  idempotency_key,
  COALESCE(tenant_id, '')
)
WHERE idempotency_key IS NOT NULL;

COMMENT ON INDEX public.ux_execution_operations_idempotency IS
  'One ExecutionOperation per (owner, type, idempotency_key, tenant); concurrent reserve converges via unique violation + reselect';
