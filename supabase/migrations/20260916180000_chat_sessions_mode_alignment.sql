-- Align chat_sessions with SQLAlchemy ChatSession.mode (chat | loop).
-- Live Prime failed INSERT with UndefinedColumn: mode.
-- Safe for populated tables: ADD COLUMN IF NOT EXISTS + default preserves rows.

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS mode VARCHAR(16) NOT NULL DEFAULT 'chat';
