-- Durable memory + knowledge graph on Postgres (SoT)
-- Safe to re-run: IF NOT EXISTS

CREATE TABLE IF NOT EXISTS public.memories (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES public.users(id),
  session_id text,
  role text NOT NULL DEFAULT 'user',
  content text NOT NULL,
  metadata jsonb DEFAULT '{}'::jsonb,
  kind text NOT NULL DEFAULT 'episodic',
  tenant_id text,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memories_user ON public.memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_session ON public.memories(session_id);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON public.memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_tenant ON public.memories(tenant_id);
CREATE INDEX IF NOT EXISTS idx_memories_created ON public.memories(created_at DESC);

ALTER TABLE public.memories ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS memories_owner_select ON public.memories;
CREATE POLICY memories_owner_select ON public.memories
  FOR SELECT USING (auth.uid()::text = user_id OR user_id = current_setting('app.user_id', true));

DROP POLICY IF EXISTS memories_owner_all ON public.memories;
CREATE POLICY memories_owner_all ON public.memories
  FOR ALL USING (auth.uid()::text = user_id OR user_id = current_setting('app.user_id', true));

CREATE TABLE IF NOT EXISTS public.knowledge_entities (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES public.users(id),
  name text NOT NULL,
  entity_type text DEFAULT 'concept',
  properties jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ke_user ON public.knowledge_entities(user_id);

CREATE TABLE IF NOT EXISTS public.knowledge_relationships (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES public.users(id),
  from_entity_id text NOT NULL REFERENCES public.knowledge_entities(id),
  to_entity_id text NOT NULL REFERENCES public.knowledge_entities(id),
  rel_type text DEFAULT 'related_to',
  properties jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kr_user ON public.knowledge_relationships(user_id);

ALTER TABLE public.knowledge_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_relationships ENABLE ROW LEVEL SECURITY;
