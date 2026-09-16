"""Cluster 8: IDOR / ownership boundaries for evidence, graph, traces."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
Path("data").mkdir(exist_ok=True)


def test_evidence_list_and_load_are_owner_scoped(tmp_path, monkeypatch):
    from governance import evidence as ev_mod
    from governance.evidence import EvidenceChain, EvidenceChainManager

    evid = tmp_path / "evidence"
    evid.mkdir()
    monkeypatch.setattr(ev_mod, "EVIDENCE_DIR", evid)

    a = EvidenceChain(goal="a", identity_context={"user_id": "user-a"})
    a._path = evid / f"{a.chain_id}.json"
    a.add_node(action="tool.x", actor_id="user-a")
    a.save()

    b = EvidenceChain(goal="b", identity_context={"user_id": "user-b"})
    b._path = evid / f"{b.chain_id}.json"
    b.add_node(action="tool.y", actor_id="user-b")
    b.save()

    listed_a = EvidenceChainManager.list_recent(50, user_id="user-a")
    ids_a = {c["chain_id"] for c in listed_a}
    assert a.chain_id in ids_a
    assert b.chain_id not in ids_a

    loaded = EvidenceChainManager.load(b.chain_id)
    assert loaded is not None
    assert not EvidenceChainManager.owned_by(loaded, "user-a")
    assert EvidenceChainManager.owned_by(loaded, "user-b")


def test_unscoped_evidence_not_listed():
    """Chains without identity owner are fail-closed for listing."""
    from governance import evidence as ev_mod
    from governance.evidence import EvidenceChain, EvidenceChainManager
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        evid = Path(td)
        # patch
        import governance.evidence as m
        old = m.EVIDENCE_DIR
        m.EVIDENCE_DIR = evid
        try:
            c = EvidenceChain(goal="orphan", identity_context={})
            c._path = evid / f"{c.chain_id}.json"
            c.save()
            listed = EvidenceChainManager.list_recent(20, user_id="anyone")
            assert all(x["chain_id"] != c.chain_id for x in listed)
        finally:
            m.EVIDENCE_DIR = old


def test_graph_entity_idor(tmp_path, monkeypatch):
    import asyncio
    url = f"sqlite+aiosqlite:///{tmp_path}/graph_idor.db"
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("DATABASE_URL", url)
    from core.config import settings
    from core.sync_session import dispose_sync_engine
    from core.database import dispose_async_engine, init_db

    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "REQUIRE_POSTGRES", False)
    dispose_sync_engine()
    dispose_async_engine()

    async def _run():
        await init_db()
        from memory.graph import KnowledgeGraph
        KnowledgeGraph._instance = None
        KnowledgeGraph._initialized = False
        kg = KnowledgeGraph()
        kg._initialized = False
        eid = await kg.add_entity("user-a", "Alpha", "concept", {})
        owned = await kg.get_entity(eid, "user-a")
        assert owned and owned["name"] == "Alpha"
        leaked = await kg.get_entity(eid, "user-b")
        assert leaked is None
        related = await kg.get_related(eid, "user-b")
        assert related == []
        dispose_async_engine()
        dispose_sync_engine()
        KnowledgeGraph._instance = None

    asyncio.run(_run())


def test_observability_list_traces_accepts_user_scope():
    from governance.observability import ObservabilityStore
    import inspect
    sig = inspect.signature(ObservabilityStore.list_traces)
    assert "user_id" in sig.parameters
