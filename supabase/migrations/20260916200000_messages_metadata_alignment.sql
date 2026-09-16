-- Align public.messages with SQLAlchemy Message.metadata_ column.
-- Live Prime failed INSERT with UndefinedColumn: messages.metadata
-- ORM (core/database.py Message):
--   metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON)
-- Optional dict → JSONB NULL (no default required; inserts may omit the column).
-- Safe for populated tables: ADD COLUMN IF NOT EXISTS, nullable, no rewrite.

ALTER TABLE public.messages
    ADD COLUMN IF NOT EXISTS metadata JSONB NULL;
