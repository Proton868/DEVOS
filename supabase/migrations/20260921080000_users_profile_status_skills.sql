-- Align users profile fields with the SQLAlchemy User model.
-- Forward-only and idempotent for existing Supabase/Postgres deployments.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS status_message VARCHAR(160);

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS skills VARCHAR(1024);
