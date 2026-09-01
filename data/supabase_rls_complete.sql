CREATE OR REPLACE FUNCTION public.is_tenant_member(tid UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (SELECT 1 FROM tenant_memberships tm WHERE tm.tenant_id = tid AND tm.user_id = auth.uid());
$$;
CREATE OR REPLACE FUNCTION public.is_tenant_admin(tid UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (SELECT 1 FROM tenant_memberships tm WHERE tm.tenant_id = tid AND tm.user_id = auth.uid() AND tm.role IN ('owner','admin'));
$$;
ALTER TABLE IF EXISTS workflows ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflows_select ON workflows;
CREATE POLICY workflows_select ON workflows FOR SELECT
  USING (user_id = auth.uid() OR (tenant_id IS NOT NULL AND is_tenant_member(tenant_id)));
ALTER TABLE IF EXISTS evidence_chains ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS evidence_select ON evidence_chains;
CREATE POLICY evidence_select ON evidence_chains FOR SELECT
  USING (user_id = auth.uid() OR (tenant_id IS NOT NULL AND is_tenant_member(tenant_id)));
ALTER TABLE IF EXISTS caraios_memories ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS memories_select ON caraios_memories;
CREATE POLICY memories_select ON caraios_memories FOR SELECT
  USING (user_id = auth.uid() OR (tenant_id IS NOT NULL AND is_tenant_member(tenant_id)));
CREATE OR REPLACE FUNCTION match_memories(
    query_embedding vector(768), match_user_id UUID, match_count INT DEFAULT 5,
    match_kind TEXT DEFAULT NULL, match_tenant_id UUID DEFAULT NULL)
RETURNS TABLE (id UUID, user_id UUID, tenant_id UUID, role TEXT, content TEXT, kind TEXT,
    session_id TEXT, metadata JSONB, created_at TIMESTAMPTZ, similarity FLOAT)
LANGUAGE plpgsql SECURITY INVOKER AS $$
BEGIN
  RETURN QUERY SELECT m.id, m.user_id, m.tenant_id, m.role, m.content, m.kind, m.session_id, m.metadata, m.created_at,
    1 - (m.embedding <=> query_embedding) AS similarity
  FROM caraios_memories m
  WHERE m.user_id = match_user_id
    AND (match_kind IS NULL OR m.kind = match_kind)
    AND (match_tenant_id IS NULL OR m.tenant_id = match_tenant_id)
  ORDER BY m.embedding <=> query_embedding LIMIT match_count;
END; $$;
