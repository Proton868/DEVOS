-- Operational observability (not authority)
CREATE TABLE IF NOT EXISTS observability_errors (
  id text PRIMARY KEY,
  component text,
  message text,
  trace_id text,
  user_id text,
  status_code integer,
  meta jsonb DEFAULT '{}'::jsonb,
  created_at double precision
);
CREATE INDEX IF NOT EXISTS idx_obs_errors_user ON observability_errors (user_id);
CREATE INDEX IF NOT EXISTS idx_obs_errors_trace ON observability_errors (trace_id);
CREATE INDEX IF NOT EXISTS idx_obs_errors_created ON observability_errors (created_at);

CREATE TABLE IF NOT EXISTS observability_traces (
  id text PRIMARY KEY,
  agent_id text,
  session_id text,
  user_id text,
  goal text,
  provider text,
  model text,
  status text,
  meta jsonb DEFAULT '{}'::jsonb,
  started_at double precision,
  ended_at double precision
);
CREATE INDEX IF NOT EXISTS idx_obs_traces_session ON observability_traces (session_id);
CREATE INDEX IF NOT EXISTS idx_obs_traces_user ON observability_traces (user_id);
