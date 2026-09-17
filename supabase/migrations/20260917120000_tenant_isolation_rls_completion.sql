-- Tenant isolation / RLS completion (defense-in-depth).
-- Backend DATABASE_URL bypasses RLS; policies protect accidental PostgREST exposure.

DROP POLICY IF EXISTS memories_owner_select ON public.memories;
DROP POLICY IF EXISTS memories_owner_all ON public.memories;
CREATE POLICY memories_owner_select ON public.memories
  FOR SELECT USING (
    user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
    OR user_id = NULLIF(current_setting('app.user_id', true), '')
  );
CREATE POLICY memories_owner_all ON public.memories
  FOR ALL
  USING (
    user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
    OR user_id = NULLIF(current_setting('app.user_id', true), '')
  )
  WITH CHECK (
    user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
    OR user_id = NULLIF(current_setting('app.user_id', true), '')
  );

DROP POLICY IF EXISTS knowledge_entities_owner ON public.knowledge_entities;
CREATE POLICY knowledge_entities_owner ON public.knowledge_entities
  FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
DROP POLICY IF EXISTS knowledge_relationships_owner ON public.knowledge_relationships;
CREATE POLICY knowledge_relationships_owner ON public.knowledge_relationships
  FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));

DROP POLICY IF EXISTS chat_sessions_owner ON public.chat_sessions;
CREATE POLICY chat_sessions_owner ON public.chat_sessions
  FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
DROP POLICY IF EXISTS messages_via_session ON public.messages;
CREATE POLICY messages_via_session ON public.messages
  FOR ALL
  USING (session_id IN (SELECT id FROM public.chat_sessions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)))
  WITH CHECK (session_id IN (SELECT id FROM public.chat_sessions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)));

DROP POLICY IF EXISTS persona_profiles_owner ON public.persona_profiles;
CREATE POLICY persona_profiles_owner ON public.persona_profiles
  FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
DROP POLICY IF EXISTS persona_experience_owner ON public.persona_experience_events;
CREATE POLICY persona_experience_owner ON public.persona_experience_events
  FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));

DROP POLICY IF EXISTS workflow_records_owner ON public.workflow_records;
CREATE POLICY workflow_records_owner ON public.workflow_records
  FOR ALL USING (COALESCE(owner_id, user_id) IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (COALESCE(owner_id, user_id) IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
DROP POLICY IF EXISTS evidence_records_owner ON public.evidence_records;
CREATE POLICY evidence_records_owner ON public.evidence_records
  FOR ALL USING (COALESCE(owner_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (COALESCE(owner_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
DROP POLICY IF EXISTS execution_jobs_owner ON public.execution_jobs;
CREATE POLICY execution_jobs_owner ON public.execution_jobs
  FOR ALL USING (COALESCE(owner_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (COALESCE(owner_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
DROP POLICY IF EXISTS execution_operations_owner ON public.execution_operations;
CREATE POLICY execution_operations_owner ON public.execution_operations
  FOR ALL USING (COALESCE(owner_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (COALESCE(owner_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));

DROP POLICY IF EXISTS mission_tasks_via_mission ON public.mission_tasks;
CREATE POLICY mission_tasks_via_mission ON public.mission_tasks
  FOR ALL USING (mission_id IN (SELECT id FROM public.missions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)))
  WITH CHECK (mission_id IN (SELECT id FROM public.missions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)));
DROP POLICY IF EXISTS task_delegations_via_mission ON public.task_delegations;
CREATE POLICY task_delegations_via_mission ON public.task_delegations
  FOR ALL USING (mission_id IN (SELECT id FROM public.missions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)))
  WITH CHECK (mission_id IN (SELECT id FROM public.missions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)));
DROP POLICY IF EXISTS agent_messages_via_mission ON public.agent_messages;
CREATE POLICY agent_messages_via_mission ON public.agent_messages
  FOR ALL USING (mission_id IN (SELECT id FROM public.missions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)))
  WITH CHECK (mission_id IN (SELECT id FROM public.missions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)));
DROP POLICY IF EXISTS agent_events_via_mission ON public.agent_events;
CREATE POLICY agent_events_via_mission ON public.agent_events
  FOR ALL
  USING (
    mission_id IN (SELECT id FROM public.missions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
    OR COALESCE(actor_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
  )
  WITH CHECK (
    mission_id IN (SELECT id FROM public.missions WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
    OR COALESCE(actor_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
  );
DROP POLICY IF EXISTS tool_executions_owner ON public.tool_executions;
CREATE POLICY tool_executions_owner ON public.tool_executions
  FOR ALL USING (COALESCE(user_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (COALESCE(user_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
DROP POLICY IF EXISTS ponytail_checks_owner ON public.ponytail_checks;
CREATE POLICY ponytail_checks_owner ON public.ponytail_checks
  FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
DROP POLICY IF EXISTS artifact_versions_via_artifact ON public.artifact_versions;
CREATE POLICY artifact_versions_via_artifact ON public.artifact_versions
  FOR ALL USING (artifact_id IN (SELECT id FROM public.artifacts WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)))
  WITH CHECK (artifact_id IN (SELECT id FROM public.artifacts WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)));

DROP POLICY IF EXISTS agents_select_all ON public.agents;
CREATE POLICY agents_select_all ON public.agents FOR SELECT USING (true);
DROP POLICY IF EXISTS agent_personas_select_all ON public.agent_personas;
CREATE POLICY agent_personas_select_all ON public.agent_personas FOR SELECT USING (true);

DROP POLICY IF EXISTS durable_capabilities_scope ON public.durable_capabilities;
CREATE POLICY durable_capabilities_scope ON public.durable_capabilities
  FOR ALL
  USING (
    COALESCE(owner_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
    OR COALESCE(tenant_id, '') IN (SELECT tenant_id FROM public.memberships WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  )
  WITH CHECK (
    COALESCE(owner_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
    OR COALESCE(tenant_id, '') IN (SELECT tenant_id FROM public.memberships WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
  );
DROP POLICY IF EXISTS worker_trust_scope ON public.worker_trust_records;
CREATE POLICY worker_trust_scope ON public.worker_trust_records
  FOR ALL
  USING (COALESCE(tenant_id, '') IN (SELECT tenant_id FROM public.memberships WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)))
  WITH CHECK (COALESCE(tenant_id, '') IN (SELECT tenant_id FROM public.memberships WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)));
DROP POLICY IF EXISTS tenants_member_select ON public.tenants;
CREATE POLICY tenants_member_select ON public.tenants
  FOR SELECT USING (
    id IN (SELECT tenant_id FROM public.memberships WHERE user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
    OR owner_user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
  );

DO $$
BEGIN
  IF to_regclass('public.secrets') IS NOT NULL THEN
    ALTER TABLE public.secrets ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS secrets_owner ON public.secrets;
    CREATE POLICY secrets_owner ON public.secrets
      FOR ALL USING (owner_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (owner_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.scripts') IS NOT NULL THEN
    ALTER TABLE public.scripts ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS scripts_owner ON public.scripts;
    CREATE POLICY scripts_owner ON public.scripts
      FOR ALL USING (owner_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (owner_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.user_settings') IS NOT NULL THEN
    ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS user_settings_owner ON public.user_settings;
    CREATE POLICY user_settings_owner ON public.user_settings
      FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.custom_endpoints') IS NOT NULL THEN
    ALTER TABLE public.custom_endpoints ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS custom_endpoints_owner ON public.custom_endpoints;
    CREATE POLICY custom_endpoints_owner ON public.custom_endpoints
      FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.sagas') IS NOT NULL THEN
    ALTER TABLE public.sagas ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS sagas_owner ON public.sagas;
    CREATE POLICY sagas_owner ON public.sagas
      FOR ALL USING (COALESCE(user_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (COALESCE(user_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.outbox_events') IS NOT NULL THEN
    ALTER TABLE public.outbox_events ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS outbox_owner_select ON public.outbox_events;
    CREATE POLICY outbox_owner_select ON public.outbox_events
      FOR SELECT USING (COALESCE(user_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.audit_log') IS NOT NULL THEN
    ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS audit_log_owner_select ON public.audit_log;
    CREATE POLICY audit_log_owner_select ON public.audit_log
      FOR SELECT USING (
        COALESCE(user_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
        OR COALESCE(actor_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
      );
  END IF;
  IF to_regclass('public.web_crawls') IS NOT NULL THEN
    ALTER TABLE public.web_crawls ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS web_crawls_owner ON public.web_crawls;
    CREATE POLICY web_crawls_owner ON public.web_crawls
      FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.carai_voice_sessions') IS NOT NULL THEN
    ALTER TABLE public.carai_voice_sessions ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS carai_voice_owner ON public.carai_voice_sessions;
    CREATE POLICY carai_voice_owner ON public.carai_voice_sessions
      FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.delivery_runtimes') IS NOT NULL THEN
    ALTER TABLE public.delivery_runtimes ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS delivery_runtimes_owner ON public.delivery_runtimes;
    CREATE POLICY delivery_runtimes_owner ON public.delivery_runtimes
      FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.delivery_shares') IS NOT NULL THEN
    ALTER TABLE public.delivery_shares ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS delivery_shares_owner ON public.delivery_shares;
    CREATE POLICY delivery_shares_owner ON public.delivery_shares
      FOR ALL USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.observability_errors') IS NOT NULL THEN
    ALTER TABLE public.observability_errors ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS observability_errors_owner ON public.observability_errors;
    CREATE POLICY observability_errors_owner ON public.observability_errors
      FOR SELECT USING (COALESCE(user_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
  IF to_regclass('public.observability_traces') IS NOT NULL THEN
    ALTER TABLE public.observability_traces ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS observability_traces_owner ON public.observability_traces;
    CREATE POLICY observability_traces_owner ON public.observability_traces
      FOR SELECT USING (COALESCE(user_id, '') IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;
END $$;
