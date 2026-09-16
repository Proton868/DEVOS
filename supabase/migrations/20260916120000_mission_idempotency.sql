-- Phase 3: scoped mission idempotency (user_id + key)
ALTER TABLE missions ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(192);

CREATE UNIQUE INDEX IF NOT EXISTS ux_missions_user_idempotency
  ON missions (user_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
