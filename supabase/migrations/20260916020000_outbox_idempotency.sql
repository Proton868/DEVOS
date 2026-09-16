ALTER TABLE public.outbox_events
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_events_idempotency_key
    ON public.outbox_events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
