"""
Governance — EvidenceChain DAG (Agency OS Master Plan §1).

Per the UCIP spec, every execution produces an EvidenceChain: a directed
acyclic graph of evidence nodes where each node records a discrete action,
its outcome, and links to predecessor nodes. This is the "who did what,
when, under whose authority, with what result" record — not just a flat
audit log, but a graph that can be walked and replayed.

The EvidenceChain is distinct from the AuditLogger (governance/ucip.py:
UCIPAuditLogger records policy decisions; EvidenceChain records execution
outcomes). Together they form the full Audit + Evidence governance sub-organs.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.evidence")

EVIDENCE_DIR = Path("data/evidence")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class EvidenceNode:
    """One atomic piece of evidence — a single action, its outcome, and
    links to what came before it. Nodes form a DAG: one node can have
    multiple predecessors (e.g. a "merge" step combining two parallel
    worker outputs) and multiple successors."""
    node_id:       str
    chain_id:      str              # which EvidenceChain this belongs to
    action:        str              # e.g. "write_python", "search_web"
    actor_id:      str              # who performed it
    delegation_chain: list[str] = field(default_factory=list)  # under whose authority
    input_hash:    Optional[str] = None  # SHA-256 of action input
    output_hash:   Optional[str] = None  # SHA-256 of action output
    status:        str = "pending"  # pending | success | failed | denied | timeout
    decision:      str = ""         # UCIP decision: APPROVE | DENY | ESCALATE
    latency_ms:    int = 0
    tokens_used:   int = 0
    error:         Optional[str] = None
    predecessor_ids: list[str] = field(default_factory=list)  # DAG edges (incoming)
    successor_ids:   list[str] = field(default_factory=list)  # DAG edges (outgoing)
    timestamp:     datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:      dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "chain_id": self.chain_id,
            "action": self.action,
            "actor_id": self.actor_id,
            "delegation_chain": self.delegation_chain,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "status": self.status,
            "decision": self.decision,
            "latency_ms": self.latency_ms,
            "tokens_used": self.tokens_used,
            "error": self.error,
            "predecessor_ids": self.predecessor_ids,
            "successor_ids": self.successor_ids,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceNode":
        return cls(
            node_id=data["node_id"],
            chain_id=data["chain_id"],
            action=data["action"],
            actor_id=data["actor_id"],
            delegation_chain=data.get("delegation_chain", []),
            input_hash=data.get("input_hash"),
            output_hash=data.get("output_hash"),
            status=data.get("status", "pending"),
            decision=data.get("decision", ""),
            latency_ms=data.get("latency_ms", 0),
            tokens_used=data.get("tokens_used", 0),
            error=data.get("error"),
            predecessor_ids=data.get("predecessor_ids", []),
            successor_ids=data.get("successor_ids", []),
            timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.now(timezone.utc),
            metadata=data.get("metadata", {}),
        )


class EvidenceChain:
    """A complete chain of evidence for one execution run (one loop, one
    worker task, one coordinated plan). Each chain is a DAG of EvidenceNodes
    persisted to disk for replay and audit."""

    def __init__(self, chain_id: Optional[str] = None, goal: str = "",
                 identity_context: Optional[dict] = None):
        self.chain_id = chain_id or str(uuid.uuid4())
        self.goal = goal
        self.identity_context = identity_context or {}
        self.nodes: dict[str, EvidenceNode] = {}
        self._root_id: Optional[str] = None
        self._path = EVIDENCE_DIR / f"{self.chain_id}.json"

    def add_node(self, action: str, actor_id: str,
                 predecessor_ids: Optional[list[str]] = None,
                 delegation_chain: Optional[list[str]] = None,
                 **kwargs) -> EvidenceNode:
        """Add a new evidence node. predecessor_ids define the DAG edges —
        if None, this is the root node (first action in the chain)."""
        node = EvidenceNode(
            node_id=str(uuid.uuid4()),
            chain_id=self.chain_id,
            action=action,
            actor_id=actor_id,
            delegation_chain=delegation_chain or [],
            predecessor_ids=predecessor_ids or [],
            **{k: v for k, v in kwargs.items()
               if k in {"input_hash", "output_hash", "status", "decision",
                        "latency_ms", "tokens_used", "error", "metadata"}},
        )
        self.nodes[node.node_id] = node

        # Update successor links on predecessors
        for pid in node.predecessor_ids:
            if pid in self.nodes:
                self.nodes[pid].successor_ids.append(node.node_id)

        if self._root_id is None and not node.predecessor_ids:
            self._root_id = node.node_id

        return node

    def complete_node(self, node_id: str, status: str, output_hash: Optional[str] = None,
                      error: Optional[str] = None, **kwargs):
        """Mark a node as complete with its final status and output."""
        if node_id not in self.nodes:
            logger.warning(f"[evidence] complete_node: unknown node {node_id}")
            return
        node = self.nodes[node_id]
        node.status = status
        if output_hash:
            node.output_hash = output_hash
        if error:
            node.error = error
        for k, v in kwargs.items():
            if hasattr(node, k):
                setattr(node, k, v)

    def walk(self, start_id: Optional[str] = None) -> list[EvidenceNode]:
        """Walk the DAG in topological order from start_id (or root)."""
        if start_id is None:
            start_id = self._root_id
        if start_id is None:
            return []

        visited: set[str] = set()
        order: list[EvidenceNode] = []

        def dfs(node_id: str):
            if node_id in visited or node_id not in self.nodes:
                return
            visited.add(node_id)
            node = self.nodes[node_id]
            for pred_id in node.predecessor_ids:
                dfs(pred_id)
            order.append(node)

        dfs(start_id)
        return order

    def get_leaf_nodes(self) -> list[EvidenceNode]:
        """Nodes with no successors (endpoints of the DAG)."""
        return [n for n in self.nodes.values() if not n.successor_ids]

    def get_root_nodes(self) -> list[EvidenceNode]:
        """Nodes with no predecessors (starting points of the DAG)."""
        return [n for n in self.nodes.values() if not n.predecessor_ids]

    def save(self):
        """Persist the entire chain to disk."""
        data = {
            "chain_id": self.chain_id,
            "goal": self.goal,
            "identity_context": self.identity_context,
            "root_id": self._root_id,
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str, indent=2))
        tmp.replace(self._path)

    @classmethod
    def load(cls, chain_id: str) -> Optional["EvidenceChain"]:
        """Load a chain from disk."""
        path = EVIDENCE_DIR / f"{chain_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            chain = cls(chain_id=data["chain_id"], goal=data.get("goal", ""),
                        identity_context=data.get("identity_context", {}))
            chain._root_id = data.get("root_id")
            chain.nodes = {nid: EvidenceNode.from_dict(nd)
                          for nid, nd in data.get("nodes", {}).items()}
            return chain
        except Exception as e:
            logger.warning(f"[evidence] failed to load chain {chain_id}: {e}")
            return None

    def to_dict(self) -> dict:
        return {
            "chain_id": self.chain_id,
            "goal": self.goal,
            "identity_context": self.identity_context,
            "root_id": self._root_id,
            "node_count": len(self.nodes),
            "nodes": [n.to_dict() for n in self.walk()],
        }

    def stats(self) -> dict:
        nodes = list(self.nodes.values())
        return {
            "chain_id": self.chain_id,
            "total_nodes": len(nodes),
            "successful": sum(1 for n in nodes if n.status == "success"),
            "failed": sum(1 for n in nodes if n.status == "failed"),
            "denied": sum(1 for n in nodes if n.status == "denied"),
            "total_latency_ms": sum(n.latency_ms for n in nodes),
            "total_tokens": sum(n.tokens_used for n in nodes),
            "max_depth": self._max_depth(),
        }

    def _max_depth(self) -> int:
        """Longest path from any root to any leaf."""
        if not self.nodes:
            return 0
        memo: dict[str, int] = {}

        def depth(node_id: str) -> int:
            if node_id in memo:
                return memo[node_id]
            node = self.nodes.get(node_id)
            if not node:
                return 0
            if not node.successor_ids:
                memo[node_id] = 1
                return 1
            max_succ = max(depth(sid) for sid in node.successor_ids) if node.successor_ids else 0
            memo[node_id] = 1 + max_succ
            return memo[node_id]

        roots = self.get_root_nodes()
        if not roots:
            return 0
        return max(depth(r.node_id) for r in roots)


class EvidenceChainManager:
    """Global manager for all evidence chains. Handles persistence,
    listing, and cleanup."""

    @staticmethod
    def create(goal: str = "", identity_context: Optional[dict] = None) -> EvidenceChain:
        return EvidenceChain(goal=goal, identity_context=identity_context)

    @staticmethod
    def load(chain_id: str) -> Optional[EvidenceChain]:
        return EvidenceChain.load(chain_id)

    @staticmethod
    def list_recent(limit: int = 50) -> list[dict]:
        """List recent chains with summary stats."""
        chains = []
        for f in sorted(EVIDENCE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text())
                nodes = data.get("nodes", {})
                chain = {
                    "chain_id": data.get("chain_id"),
                    "goal": data.get("goal", "")[:100],
                    "node_count": len(nodes),
                    "root_id": data.get("root_id"),
                }
                chains.append(chain)
                if len(chains) >= limit:
                    break
            except Exception:
                pass
        return chains

    @staticmethod
    def replay(chain_id: str) -> Optional[dict]:
        """Full replay of a chain — every node in topological order."""
        chain = EvidenceChain.load(chain_id)
        if not chain:
            return None
        return chain.to_dict()

    @staticmethod
    def cleanup_old(max_age_hours: int = 168):
        """Remove chains older than max_age_hours (default 7 days)."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        for f in EVIDENCE_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                nodes = data.get("nodes", {})
                if not nodes:
                    f.unlink()
                    continue
                latest = max(
                    datetime.fromisoformat(n.get("timestamp", "2000-01-01"))
                    for n in nodes.values()
                )
                if latest < cutoff:
                    f.unlink()
            except Exception:
                pass