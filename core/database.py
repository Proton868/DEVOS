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
    default_tenant_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
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
    await _add_column_if_missing(
        "users", "default_tenant_id",
        "ALTER TABLE users ADD COLUMN default_tenant_id VARCHAR",
    )
    await _add_column_if_missing(
        "workflow_records", "description",
        "ALTER TABLE workflow_records ADD COLUMN description TEXT DEFAULT ''",
    )
    await _add_column_if_missing(
        "workflow_records", "enabled",
        "ALTER TABLE workflow_records ADD COLUMN enabled BOOLEAN DEFAULT 1",
    )
    await _add_column_if_missing(
        "workflow_records", "version",
        "ALTER TABLE workflow_records ADD COLUMN version INTEGER DEFAULT 1",
    )
    await _add_column_if_missing(
        "workflow_records", "tenant_id",
        "ALTER TABLE workflow_records ADD COLUMN tenant_id VARCHAR",
    )
    await _add_column_if_missing(
        "evidence_records", "tenant_id",
        "ALTER TABLE evidence_records ADD COLUMN tenant_id VARCHAR",
    )
    await _add_column_if_missing(
        "execution_jobs", "workflow_id",
        "ALTER TABLE execution_jobs ADD COLUMN workflow_id VARCHAR",
    )
    await _add_column_if_missing(
        "execution_jobs", "workflow_version",
        "ALTER TABLE execution_jobs ADD COLUMN workflow_version INTEGER",
    )
    await _add_column_if_missing(
        "execution_jobs", "worker_id",
        "ALTER TABLE execution_jobs ADD COLUMN worker_id VARCHAR",
    )
    await _add_column_if_missing(
        "execution_jobs", "locked_at",
        "ALTER TABLE execution_jobs ADD COLUMN locked_at DATETIME",
    )
    await _add_column_if_missing(
        "execution_jobs", "lease_expires_at",
        "ALTER TABLE execution_jobs ADD COLUMN lease_expires_at DATETIME",
    )
    await _add_column_if_missing(
        "execution_jobs", "idempotency_key",
        "ALTER TABLE execution_jobs ADD COLUMN idempotency_key VARCHAR",
    )
    await _add_column_if_missing(
        "execution_jobs", "request_id",
        "ALTER TABLE execution_jobs ADD COLUMN request_id VARCHAR",
    )
    await _add_column_if_missing(
        "execution_jobs", "correlation",
        "ALTER TABLE execution_jobs ADD COLUMN correlation JSON",
    )
    await _add_column_if_missing(
        "agent_tasks", "hai_checkpoint",
        "ALTER TABLE agent_tasks ADD COLUMN hai_checkpoint JSON",
    )

    await _add_column_if_missing(
        "worker_trust_records", "competency",
        "ALTER TABLE worker_trust_records ADD COLUMN competency JSON",
    )
    await _add_column_if_missing(
        "worker_trust_records", "pending_promotion",
        "ALTER TABLE worker_trust_records ADD COLUMN pending_promotion JSON",
    )
    await _add_column_if_missing(
        "worker_trust_records", "promotion_expires_at",
        "ALTER TABLE worker_trust_records ADD COLUMN promotion_expires_at DATETIME",
    )
    await _add_column_if_missing(
        "worker_trust_records", "approved_by",
        "ALTER TABLE worker_trust_records ADD COLUMN approved_by VARCHAR",
    )
    await _add_column_if_missing(
        "worker_trust_records", "approved_at",
        "ALTER TABLE worker_trust_records ADD COLUMN approved_at DATETIME",
    )

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    name: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    tier: Mapped[str] = mapped_column(String(32), default="tenant_user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    meta: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)

class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class WorkflowRecord(Base):
    """Durable workflow definition. Database is the source of truth;
    runtime engines may cache but must reload from this table."""
    __tablename__ = "workflow_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Monotonic integer revision for optimistic concurrency / evidence correlation.
    # Distinct from any free-form version string stored inside definition JSON.
    version: Mapped[int] = mapped_column(Integer, default=1)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class EvidenceRecord(Base):
    __tablename__ = "evidence_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    goal: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class DurableCapability(Base):
    __tablename__ = "durable_capabilities"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    slug: Mapped[str] = mapped_column(String(256), index=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    name: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(64), default="system")
    description: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(String(32), default="medium")
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    signature: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approval_state: Mapped[str] = mapped_column(String(32), default="approved")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class ExecutionJob(Base):
    """Durable work unit. Status lifecycle:
    queued → running → succeeded|failed
    running + lease expired → queued (recoverable retry)

    For job_type=workflow, workflow_id/workflow_version identify the definition
    that was snapshotted into payload at enqueue time. Deleting the WorkflowRecord
    must not cascade-delete jobs — these columns are historical references only.
    """
    __tablename__ = "execution_jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    job_type: Mapped[str] = mapped_column(String(64), default="script")
    workflow_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    workflow_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    isolation: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    correlation: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class WorkerTrustRecord(Base):
    """Earned autonomy state. Promotion is never self-granted — only proposed
    by the trust engine and applied after human approval."""
    __tablename__ = "worker_trust_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    worker_id: Mapped[str] = mapped_column(String, index=True)
    trust_level: Mapped[str] = mapped_column(String(32), default="supervised")
    autonomy: Mapped[str] = mapped_column(String(32), default="supervised")
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    unauthorized_attempts: Mapped[int] = mapped_column(Integer, default=0)
    granted_caps: Mapped[list] = mapped_column(JSON, default=list)
    # capability slug -> {success, failure, competency (0-1), last_score}
    competency: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    # Human-gated promotion: engine proposes, human approves
    pending_promotion: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # {"autonomy": "...", "trust_level": "...", "proposed_at": "...", "reason": "..."}
    promotion_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # None = permanent until human demotes; set = temporary autonomy window
    approved_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentTaskRecord(Base):
    """Durable agent coding-task orchestration state (not ExecutionJob).

    AgentTask is session/orchestration state for the IDE coding agent.
    ExecutionJob remains the durable work unit for scripts/workflows.
    Events are kept in a bounded JSON ring for reconnect, not a second audit log.
    """
    __tablename__ = "agent_tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(String, index=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    project_id: Mapped[str] = mapped_column(String, index=True, default="default")
    session_id: Mapped[str] = mapped_column(String, index=True)
    objective: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(32), default="agent")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    current_tool: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    files_changed: Mapped[list] = mapped_column(JSON, default=list)
    tools_used: Mapped[list] = mapped_column(JSON, default=list)
    correlation_id: Mapped[str] = mapped_column(String, index=True, default=gen_id)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Bounded recent events for SSE reconnect (not Evidence substitute)
    events: Mapped[list] = mapped_column(JSON, default=list)
    hai_checkpoint: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
