-- Align chat_sessions with SQLAlchemy ChatSession.node_id / workflow_id.
-- Live Prime failed INSERT with UndefinedColumn: node_id.
-- ORM (core/database.py ChatSession):
--   node_id:     Mapped[Optional[str]] = mapped_column(String, nullable=True)
--   workflow_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
-- Unbounded String → TEXT (same as existing chat_sessions TEXT columns).
-- Safe for populated tables: ADD COLUMN IF NOT EXISTS, nullable, no rewrite.

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS node_id TEXT NULL;

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS workflow_id TEXT NULL;
