-- Execution / governance / observability durable tables (Postgres SoT)

CREATE TABLE IF NOT EXISTS public.outbox_events (
  id text PRIMARY KEY,
  event_type text NOT NULL,
  aggregate_id text,
  user_id text,
  payload jsonb DEFAULT '{}'::jsonb,
  status text DEFAULT 'pending',
  attempts int DEFAULT 0,
  last_error text,
  available_at timestamptz,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON public.outbox_events(status);
CREATE INDEX IF NOT EXISTS idx_outbox_user ON public.outbox_events(user_id);

CREATE TABLE IF NOT EXISTS public.sagas (
  id text PRIMARY KEY,
  plan_id text,
  mission_id text,
  user_id text,
  status text DEFAULT 'running',
  failure text,
  meta jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.saga_steps (
  id text PRIMARY KEY,
  saga_id text NOT NULL REFERENCES public.sagas(id),
  node_id text,
  action text,
  status text DEFAULT 'pending',
  phase text,
  attempts int DEFAULT 0,
  error text,
  evidence_id text,
  meta jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.delivery_runtimes (
  runtime_id text PRIMARY KEY,
  user_id text NOT NULL,
  project_id text NOT NULL,
  status text,
  pid int,
  port int,
  command text,
  cwd text,
  app_type text,
  revision text,
  isolation_mode text,
  last_error text,
  created_at double precision,
  started_at double precision,
  stopped_at double precision,
  meta jsonb DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS public.delivery_shares (
  share_id text PRIMARY KEY,
  user_id text NOT NULL,
  project_id text NOT NULL,
  path text NOT NULL,
  permission text,
  status text,
  revision_hash text,
  created_at double precision,
  expires_at double precision,
  revoked_at double precision
);

CREATE TABLE IF NOT EXISTS public.audit_log (
  id text PRIMARY KEY,
  event_type text NOT NULL,
  actor_id text NOT NULL,
  actor_type text,
  tenant_id text,
  user_id text,
  action text,
  resource text,
  resource_id text,
  mission_id text,
  task_id text,
  result text,
  evidence text,
  details jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON public.audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON public.audit_log(actor_id);

CREATE TABLE IF NOT EXISTS public.trace_spans (
  id text PRIMARY KEY,
  trace_id text NOT NULL,
  span_id text NOT NULL,
  parent_span_id text,
  name text,
  status text,
  attrs jsonb DEFAULT '{}'::jsonb,
  started_at double precision,
  ended_at double precision
);
CREATE INDEX IF NOT EXISTS idx_trace_id ON public.trace_spans(trace_id);

CREATE TABLE IF NOT EXISTS public.web_crawls (
  crawl_id text PRIMARY KEY,
  user_id text NOT NULL,
  root_url text,
  normalized_root_url text,
  status text,
  meta jsonb DEFAULT '{}'::jsonb,
  created_at double precision,
  updated_at double precision
);

CREATE TABLE IF NOT EXISTS public.carai_voice_sessions (
  id text PRIMARY KEY,
  user_id text NOT NULL,
  status text,
  meta jsonb DEFAULT '{}'::jsonb,
  transcript jsonb DEFAULT '[]'::jsonb,
  created_at double precision,
  updated_at double precision
);

ALTER TABLE public.outbox_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_crawls ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.carai_voice_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.delivery_runtimes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.delivery_shares ENABLE ROW LEVEL SECURITY;
