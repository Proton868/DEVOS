-- World/tenant isolation: RLS policies for tables created after
-- 20260917120000_tenant_isolation_rls_completion.
--
-- Defense-in-depth for accidental PostgREST / authenticated-role exposure.
-- Backend DATABASE_URL (table owner / service role) bypasses RLS; application
-- routes remain the primary ownership check.
--
-- Invariant: world_id ≡ tenant_id. Policies bind rows to the authenticated
-- app user (users.supabase_id = auth.uid()) and never USING (true).
--
-- Idempotent: ENABLE RLS + DROP POLICY IF EXISTS + CREATE POLICY.
-- Does not drop tables, rewrite data, or mutate schema_migrations history.

DO $$
BEGIN
  -- automation_run_records: owner_id + optional tenant_id (world)
  IF to_regclass('public.automation_run_records') IS NOT NULL THEN
    ALTER TABLE public.automation_run_records ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS automation_run_records_owner ON public.automation_run_records;
    CREATE POLICY automation_run_records_owner ON public.automation_run_records
      FOR ALL
      USING (
        COALESCE(owner_id, '') IN (
          SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
        )
      )
      WITH CHECK (
        COALESCE(owner_id, '') IN (
          SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
        )
      );
  END IF;

  -- agentic runtime checkpoints
  IF to_regclass('public.agentic_runtime_checkpoints') IS NOT NULL THEN
    ALTER TABLE public.agentic_runtime_checkpoints ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS agentic_runtime_checkpoints_owner ON public.agentic_runtime_checkpoints;
    CREATE POLICY agentic_runtime_checkpoints_owner ON public.agentic_runtime_checkpoints
      FOR ALL
      USING (
        COALESCE(owner_id, '') IN (
          SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
        )
      )
      WITH CHECK (
        COALESCE(owner_id, '') IN (
          SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
        )
      );
  END IF;

  -- governed maintenance
  IF to_regclass('public.maintenance_requests') IS NOT NULL THEN
    ALTER TABLE public.maintenance_requests ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS maintenance_requests_owner ON public.maintenance_requests;
    CREATE POLICY maintenance_requests_owner ON public.maintenance_requests
      FOR ALL
      USING (
        COALESCE(owner_id, '') IN (
          SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
        )
      )
      WITH CHECK (
        COALESCE(owner_id, '') IN (
          SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
        )
      );
  END IF;

  IF to_regclass('public.maintenance_actions') IS NOT NULL THEN
    ALTER TABLE public.maintenance_actions ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS maintenance_actions_via_request ON public.maintenance_actions;
    CREATE POLICY maintenance_actions_via_request ON public.maintenance_actions
      FOR ALL
      USING (
        maintenance_request_id IN (
          SELECT id FROM public.maintenance_requests
          WHERE COALESCE(owner_id, '') IN (
            SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
          )
        )
      )
      WITH CHECK (
        maintenance_request_id IN (
          SELECT id FROM public.maintenance_requests
          WHERE COALESCE(owner_id, '') IN (
            SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
          )
        )
      );
  END IF;

  -- incidents
  IF to_regclass('public.incidents') IS NOT NULL THEN
    ALTER TABLE public.incidents ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS incidents_owner ON public.incidents;
    CREATE POLICY incidents_owner ON public.incidents
      FOR ALL
      USING (
        COALESCE(owner_id, '') IN (
          SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
        )
      )
      WITH CHECK (
        COALESCE(owner_id, '') IN (
          SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
        )
      );
  END IF;

  -- saga_steps via parent saga (sagas already owner-scoped)
  IF to_regclass('public.saga_steps') IS NOT NULL THEN
    ALTER TABLE public.saga_steps ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS saga_steps_via_saga ON public.saga_steps;
    CREATE POLICY saga_steps_via_saga ON public.saga_steps
      FOR ALL
      USING (
        saga_id IN (
          SELECT id FROM public.sagas
          WHERE COALESCE(user_id, '') IN (
            SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
          )
        )
      )
      WITH CHECK (
        saga_id IN (
          SELECT id FROM public.sagas
          WHERE COALESCE(user_id, '') IN (
            SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
          )
        )
      );
  END IF;

  -- web crawl children
  IF to_regclass('public.web_crawl_pages') IS NOT NULL THEN
    ALTER TABLE public.web_crawl_pages ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS web_crawl_pages_via_crawl ON public.web_crawl_pages;
    CREATE POLICY web_crawl_pages_via_crawl ON public.web_crawl_pages
      FOR ALL
      USING (
        crawl_id IN (
          SELECT crawl_id FROM public.web_crawls
          WHERE user_id IN (
            SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
          )
        )
      )
      WITH CHECK (
        crawl_id IN (
          SELECT crawl_id FROM public.web_crawls
          WHERE user_id IN (
            SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
          )
        )
      );
  END IF;

  IF to_regclass('public.web_crawl_events') IS NOT NULL THEN
    ALTER TABLE public.web_crawl_events ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS web_crawl_events_via_crawl ON public.web_crawl_events;
    CREATE POLICY web_crawl_events_via_crawl ON public.web_crawl_events
      FOR ALL
      USING (
        crawl_id IN (
          SELECT crawl_id FROM public.web_crawls
          WHERE user_id IN (
            SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
          )
        )
      )
      WITH CHECK (
        crawl_id IN (
          SELECT crawl_id FROM public.web_crawls
          WHERE user_id IN (
            SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
          )
        )
      );
  END IF;

  -- trace_spans via observability_traces.trace identity / user_id in attrs is not
  -- authoritative; bind through observability_traces.id = trace_id when present.
  IF to_regclass('public.trace_spans') IS NOT NULL
     AND to_regclass('public.observability_traces') IS NOT NULL THEN
    ALTER TABLE public.trace_spans ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS trace_spans_via_trace ON public.trace_spans;
    CREATE POLICY trace_spans_via_trace ON public.trace_spans
      FOR SELECT
      USING (
        trace_id IN (
          SELECT id FROM public.observability_traces
          WHERE COALESCE(user_id, '') IN (
            SELECT id FROM public.users WHERE supabase_id = auth.uid()::text
          )
        )
      );
  ELSIF to_regclass('public.trace_spans') IS NOT NULL THEN
    ALTER TABLE public.trace_spans ENABLE ROW LEVEL SECURITY;
  END IF;
END $$;
