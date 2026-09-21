-- Close RLS gaps for notes, documents, workspace_layouts (and script_runs if present).
-- Backend DATABASE_URL still bypasses RLS; policies defend PostgREST/JWT exposure.

DO $$
BEGIN
  IF to_regclass('public.notes') IS NOT NULL THEN
    ALTER TABLE public.notes ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS notes_owner ON public.notes;
    CREATE POLICY notes_owner ON public.notes
      FOR ALL
      USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;

  IF to_regclass('public.documents') IS NOT NULL THEN
    ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS documents_owner ON public.documents;
    CREATE POLICY documents_owner ON public.documents
      FOR ALL
      USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;

  IF to_regclass('public.workspace_layouts') IS NOT NULL THEN
    ALTER TABLE public.workspace_layouts ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS workspace_layouts_owner ON public.workspace_layouts;
    CREATE POLICY workspace_layouts_owner ON public.workspace_layouts
      FOR ALL
      USING (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text))
      WITH CHECK (user_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text));
  END IF;

  IF to_regclass('public.script_runs') IS NOT NULL THEN
    ALTER TABLE public.script_runs ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS script_runs_via_script ON public.script_runs;
    CREATE POLICY script_runs_via_script ON public.script_runs
      FOR ALL
      USING (
        script_id IN (
          SELECT id FROM public.scripts
          WHERE owner_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
        )
      )
      WITH CHECK (
        script_id IN (
          SELECT id FROM public.scripts
          WHERE owner_id IN (SELECT id FROM public.users WHERE supabase_id = auth.uid()::text)
        )
      );
  END IF;

  IF to_regclass('public.script_chains') IS NOT NULL THEN
    ALTER TABLE public.script_chains ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS script_chains_owner ON public.script_chains;
    CREATE POLICY script_chains_owner ON public.script_chains
      FOR ALL
      USING (
        parent_script_id IN (
          SELECT id FROM public.scripts
          WHERE owner_id IN (
            SELECT id FROM public.users
            WHERE supabase_id = auth.uid()::text
          )
        )
        AND child_script_id IN (
          SELECT id FROM public.scripts
          WHERE owner_id IN (
            SELECT id FROM public.users
            WHERE supabase_id = auth.uid()::text
          )
        )
      )
      WITH CHECK (
        parent_script_id IN (
          SELECT id FROM public.scripts
          WHERE owner_id IN (
            SELECT id FROM public.users
            WHERE supabase_id = auth.uid()::text
          )
        )
        AND child_script_id IN (
          SELECT id FROM public.scripts
          WHERE owner_id IN (
            SELECT id FROM public.users
            WHERE supabase_id = auth.uid()::text
          )
        )
      );
  END IF;
END $$;
