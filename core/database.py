"""DevOS Database Models"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Text, Boolean, Integer, DateTime, JSON, ForeignKey
from core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

def gen_id(): return str(uuid.uuid4())

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String)
    # Supabase's own user UUID ('sub' claim on its access tokens), set the
    # first time this person authenticates via Supabase (see
    # api/routes/auth.py's _sync_supabase_user). Nullable because
    # local-only accounts (created by _create_admin, or via the local
    # /api/auth/login path when Supabase isn't configured) never get one.
    # Kept distinct from `id` (DevOS's own primary key) rather than reusing
    # Supabase's UUID as the row id, so existing local accounts don't need
    # their primary key rewritten (and every FK referencing users.id) just
    # to link a Supabase identity onto them.
    supabase_id: Mapped[Optional[str]] = mapped_column(String, unique=True, index=True, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    sessions: Mapped[list["ChatSession"]] = relationship(back_populates="user")
    scripts: Mapped[list["Script"]] = relationship(back_populates="owner")
    secrets: Mapped[list["Secret"]] = relationship(back_populates="owner")

class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(256), default="New Chat")
    provider: Mapped[str] = mapped_column(String(32), default="ollama")
    model: Mapped[str] = mapped_column(String(128), default="")
    mode: Mapped[str] = mapped_column(String(16), default="chat")  # chat | loop
    system_prompt: Mapped[Optional[str]] = mapped_column(Text)
    # Node-scoped chat: when set, this session is pinned to a specific
    # workflow node so it "remembers everything about it" independently
    # of other nodes. NULL means a general-purpose chat session.
    node_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    workflow_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    user: Mapped["User"] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(back_populates="session", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    session: Mapped["ChatSession"] = relationship(back_populates="messages")

class Script(Base):
    __tablename__ = "scripts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[Optional[str]] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(32), default="python")
    schedule_type: Mapped[str] = mapped_column(String(16), default="manual")
    schedule_value: Mapped[Optional[str]] = mapped_column(String(128))
    notify_on_success: Mapped[str] = mapped_column(String(32), default="none")
    notify_on_failure: Mapped[str] = mapped_column(String(32), default="none")
    # Retry policy for failed runs (G9): "none" = 1 attempt, "once" = 1 retry
    # (2 attempts total), "twice" = 2 retries (3 attempts total). Read by
    # execution/script_runner.py's run_and_record().
    retry_policy: Mapped[str] = mapped_column(String(16), default="none")
    webhook_token: Mapped[str] = mapped_column(String, default=gen_id)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    owner: Mapped["User"] = relationship(back_populates="scripts")
    runs: Mapped[list["ScriptRun"]] = relationship(back_populates="script", cascade="all, delete-orphan")

class ScriptChain(Base):
    """Flow script chaining (G8) — run a child script automatically after a
    parent script finishes. `condition` gates whether the child runs:
    'on_success' (default) or 'on_failure', giving basic conditional
    branching without a full workflow-graph engine."""
    __tablename__ = "script_chains"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    parent_script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id"))
    child_script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id"))
    condition: Mapped[str] = mapped_column(String(16), default="on_success")  # on_success | on_failure
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class ScriptRun(Base):
    __tablename__ = "script_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id"))
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="running")
    stdout: Mapped[Optional[str]] = mapped_column(Text)
    stderr: Mapped[Optional[str]] = mapped_column(Text)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    loop_id: Mapped[Optional[str]] = mapped_column(String)  # Links run back to Brain loop
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    script: Mapped["Script"] = relationship(back_populates="runs")

class Note(Base):
    __tablename__ = "notes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(256), default="Untitled")
    content: Mapped[str] = mapped_column(Text, default="")
    doc_type: Mapped[str] = mapped_column(String(32), default="markdown")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class Secret(Base):
    """Encrypted credential storage for Flow scripts — the real gap found
    in record.md Session 22: the frontend's FlowPanel expected a /secrets
    API that never existed, and ExecutionLayer.run() already accepted a
    `secrets` dict parameter that nothing ever populated. Values are
    encrypted at rest (see governance/secrets_vault.py) — this table never
    stores plaintext."""
    __tablename__ = "secrets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(128))          # e.g. "STRIPE_API_KEY" -- referenced by scripts as SECRET_<name>
    description: Mapped[Optional[str]] = mapped_column(Text)
    encrypted_value: Mapped[str] = mapped_column(Text)        # Fernet ciphertext, never plaintext
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    owner: Mapped["User"] = relationship(back_populates="secrets")

class UserSettings(Base):
    """Per-user key-value settings (theme, density, font, etc.) — persisted
    server-side so preferences survive browser/device changes."""
    __tablename__ = "user_settings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class WorkspaceLayout(Base):
    """Named workspace layout snapshots — window/panel positions the user can
    save and restore (e.g. "Workflow Builder", "Debugging")."""
    __tablename__ = "workspace_layouts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(128))
    layout_json: Mapped[dict] = mapped_column(JSON)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_missing_columns(conn)

async def _migrate_missing_columns(conn):
    """Base.metadata.create_all only creates missing TABLES, never ALTERs
    existing ones -- so columns added to a model after the table already
    exists on disk (e.g. Script.retry_policy, added for G9; User.supabase_id,
    added for the Supabase-auth integration) need an explicit ALTER TABLE
    here, following the same PRAGMA table_info() pattern memory/store.py
    already uses for its own schema migrations.

    Each ALTER TABLE is wrapped in try/except OperationalError and rechecked
    against a fresh PRAGMA read rather than trusting the single columns set
    read at the top of the function (security-audit fix, P6d): if two
    workers/processes call init_db() concurrently on first boot, both can
    observe "column missing" from PRAGMA before either has run its ALTER
    TABLE, and SQLite has no `ADD COLUMN IF NOT EXISTS` — the loser of the
    race would previously crash the whole startup with 'duplicate column
    name'. Swallowing that specific error (and only that one) makes the
    migration idempotent/safe under concurrent startup without masking any
    other schema problem."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    async def _add_column_if_missing(table: str, column: str, ddl: str):
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        cols = {row[1] for row in result.fetchall()}
        if column in cols:
            return
        try:
            await conn.execute(text(ddl))
        except OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

    await _add_column_if_missing(
        "scripts", "retry_policy",
        "ALTER TABLE scripts ADD COLUMN retry_policy VARCHAR(16) DEFAULT 'none'",
    )
    await _add_column_if_missing(
        "users", "supabase_id",
        "ALTER TABLE users ADD COLUMN supabase_id VARCHAR",
    )
    await _add_column_if_missing(
        "chat_sessions", "node_id",
        "ALTER TABLE chat_sessions ADD COLUMN node_id VARCHAR",
    )
    await _add_column_if_missing(
        "chat_sessions", "workflow_id",
        "ALTER TABLE chat_sessions ADD COLUMN workflow_id VARCHAR",
    )

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
