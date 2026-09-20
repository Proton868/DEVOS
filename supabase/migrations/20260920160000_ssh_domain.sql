-- DevOS SSH domain model (owner-scoped). Credentials reference secrets; no plaintext keys.

CREATE TABLE IF NOT EXISTS public.ssh_host_identities (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    hostname TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 22,
    host_key TEXT NOT NULL,
    metadata JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ssh_host_identities_owner ON public.ssh_host_identities (owner_id);
CREATE INDEX IF NOT EXISTS idx_ssh_host_identities_host_key ON public.ssh_host_identities (owner_id, host_key);

CREATE TABLE IF NOT EXISTS public.ssh_credential_refs (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    name TEXT NOT NULL,
    auth_method TEXT NOT NULL,
    secret_id TEXT NOT NULL,
    passphrase_secret_id TEXT NULL,
    public_metadata JSONB NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    revoked_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ssh_credential_refs_owner ON public.ssh_credential_refs (owner_id);
CREATE INDEX IF NOT EXISTS idx_ssh_credential_refs_secret ON public.ssh_credential_refs (secret_id);

CREATE TABLE IF NOT EXISTS public.ssh_known_hosts (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    host_identity_id TEXT NOT NULL,
    key_type TEXT NOT NULL,
    fingerprint_sha256 TEXT NOT NULL,
    public_key TEXT NULL,
    trust_state TEXT NOT NULL DEFAULT 'pinned',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ssh_known_hosts_owner ON public.ssh_known_hosts (owner_id);
CREATE INDEX IF NOT EXISTS idx_ssh_known_hosts_host ON public.ssh_known_hosts (host_identity_id);

CREATE TABLE IF NOT EXISTS public.ssh_connections (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    workspace_id TEXT NULL,
    label TEXT NOT NULL DEFAULT '',
    host_identity_id TEXT NOT NULL,
    username TEXT NOT NULL,
    auth_method TEXT NOT NULL DEFAULT 'private_key',
    credential_ref_id TEXT NULL,
    agent_forwarding BOOLEAN NOT NULL DEFAULT FALSE,
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    health_status TEXT NOT NULL DEFAULT 'unknown',
    last_health_at TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    metadata JSONB NULL,
    created_by TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ssh_connections_owner ON public.ssh_connections (owner_id);
CREATE INDEX IF NOT EXISTS idx_ssh_connections_host ON public.ssh_connections (host_identity_id);

CREATE TABLE IF NOT EXISTS public.ssh_sessions (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    connection_id TEXT NOT NULL,
    host_identity_id TEXT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    mode TEXT NOT NULL DEFAULT 'interactive',
    actor_id TEXT NULL,
    agent_id TEXT NULL,
    remote_username TEXT NULL,
    client_info JSONB NULL,
    started_at TIMESTAMPTZ NULL,
    ended_at TIMESTAMPTZ NULL,
    last_activity_at TIMESTAMPTZ NULL,
    close_reason TEXT NULL,
    evidence_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ssh_sessions_owner ON public.ssh_sessions (owner_id);
CREATE INDEX IF NOT EXISTS idx_ssh_sessions_conn ON public.ssh_sessions (connection_id);

CREATE TABLE IF NOT EXISTS public.ssh_execution_records (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    connection_id TEXT NOT NULL,
    session_id TEXT NULL,
    operation_id TEXT NULL,
    job_id TEXT NULL,
    actor_id TEXT NULL,
    agent_id TEXT NULL,
    command TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    exit_code INTEGER NULL,
    stdout_ref TEXT NULL,
    stderr_ref TEXT NULL,
    evidence_id TEXT NULL,
    started_at TIMESTAMPTZ NULL,
    ended_at TIMESTAMPTZ NULL,
    error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ssh_exec_owner ON public.ssh_execution_records (owner_id);
CREATE INDEX IF NOT EXISTS idx_ssh_exec_conn ON public.ssh_execution_records (connection_id);

CREATE TABLE IF NOT EXISTS public.ssh_file_transfer_jobs (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    connection_id TEXT NOT NULL,
    session_id TEXT NULL,
    direction TEXT NOT NULL,
    local_path TEXT NULL,
    remote_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    bytes_transferred INTEGER NULL,
    evidence_id TEXT NULL,
    error TEXT NULL,
    started_at TIMESTAMPTZ NULL,
    ended_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ssh_xfer_owner ON public.ssh_file_transfer_jobs (owner_id);

-- RLS (defense-in-depth; backend service role may bypass)
ALTER TABLE public.ssh_host_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ssh_credential_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ssh_known_hosts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ssh_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ssh_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ssh_execution_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ssh_file_transfer_jobs ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
  tbl TEXT;
BEGIN
  FOREACH tbl IN ARRAY ARRAY[
    'ssh_host_identities', 'ssh_credential_refs', 'ssh_known_hosts',
    'ssh_connections', 'ssh_sessions', 'ssh_execution_records', 'ssh_file_transfer_jobs'
  ]
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I_owner_all ON public.%I', tbl, tbl);
    EXECUTE format(
      'CREATE POLICY %I_owner_all ON public.%I FOR ALL
       USING (
         owner_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
         OR owner_id = NULLIF(current_setting(''app.user_id'', true), '''')
       )
       WITH CHECK (
         owner_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
         OR owner_id = NULLIF(current_setting(''app.user_id'', true), '''')
       )', tbl, tbl);
  END LOOP;
END $$;
