"""Test EvidenceChain — DAG-based evidence recording and replay."""
import json
import pytest
from datetime import datetime

from governance.evidence import (
    EvidenceNode, EvidenceChain, EvidenceChainManager, EVIDENCE_DIR,
)


class TestEvidenceNode:
    def test_node_creation(self):
        node = EvidenceNode(
            node_id="n1", chain_id="c1", action="write_python",
            actor_id="user-1",
        )
        assert node.node_id == "n1"
        assert node.status == "pending"
        assert node.predecessor_ids == []
        assert node.successor_ids == []

    def test_to_dict(self):
        node = EvidenceNode(
            node_id="n1", chain_id="c1", action="write_python",
            actor_id="user-1", status="success", latency_ms=100,
        )
        d = node.to_dict()
        assert d["node_id"] == "n1"
        assert d["status"] == "success"
        assert d["latency_ms"] == 100

    def test_from_dict(self):
        node = EvidenceNode.from_dict({
            "node_id": "n1", "chain_id": "c1", "action": "write_python",
            "actor_id": "user-1", "status": "success",
            "timestamp": "2026-01-01T00:00:00",
        })
        assert node.node_id == "n1"
        assert node.status == "success"


class TestEvidenceChain:
    def test_create_chain(self):
        chain = EvidenceChain(goal="Test goal")
        assert chain.chain_id is not None
        assert chain.goal == "Test goal"

    def test_add_root_node(self):
        chain = EvidenceChain(goal="Test")
        node = chain.add_node(action="write_python", actor_id="user-1")
        assert node.node_id is not None
        assert node.chain_id == chain.chain_id
        assert chain._root_id == node.node_id

    def test_add_child_node(self):
        chain = EvidenceChain(goal="Test")
        root = chain.add_node(action="write_python", actor_id="user-1")
        child = chain.add_node(
            action="search_web", actor_id="user-1",
            predecessor_ids=[root.node_id],
        )
        assert root.node_id in child.predecessor_ids
        assert child.node_id in root.successor_ids

    def test_complete_node(self):
        chain = EvidenceChain(goal="Test")
        node = chain.add_node(action="write_python", actor_id="user-1")
        chain.complete_node(node.node_id, status="success", output_hash="abc123")
        assert node.status == "success"
        assert node.output_hash == "abc123"

    def test_complete_unknown_node(self):
        chain = EvidenceChain(goal="Test")
        # Should not raise
        chain.complete_node("nonexistent", status="success")

    def test_walk_linear(self):
        """walk() follows predecessor links; start from the leaf node to get full chain."""
        chain = EvidenceChain(goal="Test")
        n1 = chain.add_node(action="step1", actor_id="user-1")
        n2 = chain.add_node(action="step2", actor_id="user-1",
                            predecessor_ids=[n1.node_id])
        n3 = chain.add_node(action="step3", actor_id="user-1",
                            predecessor_ids=[n2.node_id])

        # Walk from the leaf to get the full chain via predecessor links
        order = chain.walk(start_id=n3.node_id)
        assert len(order) == 3
        ids = [n.node_id for n in order]
        # n1 and n2 both appear before n3 (predecessors are visited first)
        assert ids.index(n1.node_id) < ids.index(n3.node_id)
        assert ids.index(n2.node_id) < ids.index(n3.node_id)

    def test_walk_branching(self):
        """DFS walk from merge node follows all predecessor paths."""
        chain = EvidenceChain(goal="Test")
        n1 = chain.add_node(action="root", actor_id="user-1")
        n2a = chain.add_node(action="branch-a", actor_id="user-1",
                             predecessor_ids=[n1.node_id])
        n2b = chain.add_node(action="branch-b", actor_id="user-1",
                             predecessor_ids=[n1.node_id])
        n3 = chain.add_node(action="merge", actor_id="user-1",
                            predecessor_ids=[n2a.node_id, n2b.node_id])

        # Walk from merge node to get all predecessors
        order = chain.walk(start_id=n3.node_id)
        assert len(order) == 4
        ids = [n.node_id for n in order]
        # n3 must come after both n2a and n2b
        assert ids.index(n2a.node_id) < ids.index(n3.node_id)
        assert ids.index(n2b.node_id) < ids.index(n3.node_id)

    def test_get_leaf_nodes(self):
        chain = EvidenceChain(goal="Test")
        n1 = chain.add_node(action="step1", actor_id="user-1")
        n2 = chain.add_node(action="step2", actor_id="user-1",
                            predecessor_ids=[n1.node_id])
        leaves = chain.get_leaf_nodes()
        assert len(leaves) == 1
        assert leaves[0].node_id == n2.node_id

    def test_get_root_nodes(self):
        chain = EvidenceChain(goal="Test")
        chain.add_node(action="root1", actor_id="user-1")
        chain.add_node(action="root2", actor_id="user-1")
        roots = chain.get_root_nodes()
        assert len(roots) == 2

    def test_save_and_load(self):
        chain = EvidenceChain(goal="Test persistence")
        n1 = chain.add_node(action="write_python", actor_id="user-1")
        chain.complete_node(n1.node_id, status="success")
        chain.save()

        loaded = EvidenceChain.load(chain.chain_id)
        assert loaded is not None
        assert loaded.goal == chain.goal
        assert len(loaded.nodes) == 1
        assert loaded.nodes[n1.node_id].status == "success"

    def test_load_nonexistent(self):
        assert EvidenceChain.load("nonexistent-id") is None

    def test_stats(self):
        chain = EvidenceChain(goal="Stats test")
        n1 = chain.add_node(action="step1", actor_id="user-1")
        chain.complete_node(n1.node_id, status="success", latency_ms=100)
        n2 = chain.add_node(action="step2", actor_id="user-1",
                            predecessor_ids=[n1.node_id])
        chain.complete_node(n2.node_id, status="failed", latency_ms=200)

        stats = chain.stats()
        assert stats["total_nodes"] == 2
        assert stats["successful"] == 1
        assert stats["failed"] == 1
        assert stats["total_latency_ms"] == 300
        # max_depth from root (n1) to leaf (n2) = 2
        assert stats["max_depth"] >= 2


class TestEvidenceChainManager:
    def test_create(self):
        chain = EvidenceChainManager.create(goal="Manager test")
        assert chain.chain_id is not None

    def test_replay(self):
        chain = EvidenceChainManager.create(goal="Replay test")
        chain.add_node(action="write_python", actor_id="user-1")
        chain.save()

        replay = EvidenceChainManager.replay(chain.chain_id)
        assert replay is not None
        assert replay["node_count"] == 1

    def test_replay_nonexistent(self):
        assert EvidenceChainManager.replay("nonexistent") is None

    def test_list_recent(self):
        chain = EvidenceChainManager.create(goal="List test")
        chain.add_node(action="write_python", actor_id="user-1")
        chain.save()

        recent = EvidenceChainManager.list_recent(limit=10)
        assert len(recent) > 0
        assert any(c["chain_id"] == chain.chain_id for c in recent)