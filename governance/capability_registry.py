"""
Governance — Capability Registry (Agency OS Master Plan §1).

Every capability the system can perform is registered here as a formal
CapabilityDescriptor: a UCIP-compatible manifest that declares what the
capability does, what inputs/outputs it expects, its trust profile, and
which model binding it requires.

Capabilities are Governance objects — Workers consume them, the Cognitive
System discovers them, Runtime executes them, but Governance owns and
defines them. This is the single source of truth for "what can this system do?"
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("devos.capabilities")


class CapabilityCategory(str, Enum):
    EXECUTION  = "execution"   # code execution (Python, Bash, Node)
    MEMORY     = "memory"      # read/write/query memory
    FILESYSTEM = "filesystem"  # read/write/delete files
    SEARCH     = "search"      # web search, semantic search
    NETWORK    = "network"     # outbound API calls
    AGENT      = "agent"       # spawn/delegate to other agents
    VCS        = "vcs"         # version control operations
    SYSTEM     = "system"      # shell, secrets, privileged ops


class CapabilityRisk(str, Enum):
    LOW      = "low"       # Read-only, no side effects
    MEDIUM   = "medium"    # Write operations, reversible
    HIGH     = "high"      # Irreversible, network, or spawn
    CRITICAL = "critical"  # Always requires HITL + audit


@dataclass
class CapabilityDescriptor:
    """A formal UCIP-compatible capability manifest. This is what gets
    registered in the Capability Registry and what Workers declare they
    can perform. Every field maps to the UCIP ExecutionPlan schema."""
    slug:             str              # unique identifier, e.g. "ucip:execution.python"
    name:             str              # human-readable name
    category:         CapabilityCategory
    description:      str
    risk:             CapabilityRisk = CapabilityRisk.MEDIUM
    input_schema:     dict = field(default_factory=dict)   # JSON Schema
    output_schema:    dict = field(default_factory=dict)   # JSON Schema
    trust_required:   str = "operator"  # minimum trust tier
    timeout_s:        int = 30
    max_retries:      int = 3
    is_reversible:    bool = True
    requires_hitl:    bool = False
    requires_network: bool = False
    model_binding:    Optional[str] = None  # preferred model for this capability
    version:          str = "1.0.0"
    signature:        Optional[str] = None  # cryptographic signature
    metadata:         dict = field(default_factory=dict)
    created_at:       datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at:       datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "category": self.category.value,
            "description": self.description,
            "risk": self.risk.value,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "trust_required": self.trust_required,
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
            "is_reversible": self.is_reversible,
            "requires_hitl": self.requires_hitl,
            "requires_network": self.requires_network,
            "model_binding": self.model_binding,
            "version": self.version,
            "signature": self.signature,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def sign(self, secret_key: str) -> str:
        """Cryptographically sign this capability manifest."""
        payload = self._signable_payload()
        self.signature = hashlib.sha256(
            f"{payload}:{secret_key}".encode()
        ).hexdigest()
        return self.signature

    def verify(self, secret_key: str) -> bool:
        """Verify the signature matches."""
        if not self.signature:
            return False
        payload = self._signable_payload()
        expected = hashlib.sha256(
            f"{payload}:{secret_key}".encode()
        ).hexdigest()
        return self.signature == expected

    def _signable_payload(self) -> str:
        """Build the payload string for signing, excluding the signature itself
        to avoid a circular dependency where the hash includes the hash."""
        d = self.to_dict()
        d.pop("signature", None)
        return f"{self.slug}:{self.version}:{d}"


class CapabilityRegistry:
    """Singleton registry of all capabilities. Every capability the system
    knows about is registered here. Workers, the Cognitive System, and the
    UI all query this registry to discover what's available."""

    _instance = None
    _registry: dict[str, CapabilityDescriptor] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._registry = {}
            cls._instance._bootstrap()
        return cls._instance

    def _bootstrap(self):
        """Register the built-in capabilities that every Agency OS instance
        ships with. These match the existing TOOL_REGISTRY in
        governance/tool_contracts.py — CapabilityRegistry is the formal
        UCIP layer, ToolRegistry is the execution contract layer."""
        builtins = [
            CapabilityDescriptor(
                slug="ucip:execution.python", name="Python Execution",
                category=CapabilityCategory.EXECUTION,
                description="Execute Python code in a sandboxed environment",
                risk=CapabilityRisk.MEDIUM,
                input_schema={"required": ["code"], "properties": {"code": {"type": "string"}}},
                output_schema={"required": ["status", "stdout", "exit_code", "duration_ms"],
                               "properties": {"status": {"type": "string"}, "stdout": {"type": "string"},
                                              "exit_code": {"type": "integer"}, "duration_ms": {"type": "integer"}}},
                timeout_s=60, max_retries=3, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:execution.bash", name="Bash Execution",
                category=CapabilityCategory.EXECUTION,
                description="Execute Bash script in a sandboxed environment",
                risk=CapabilityRisk.MEDIUM,
                input_schema={"required": ["code"], "properties": {"code": {"type": "string"}}},
                output_schema={"required": ["status", "stdout", "exit_code", "duration_ms"],
                               "properties": {"status": {"type": "string"}, "stdout": {"type": "string"},
                                              "exit_code": {"type": "integer"}, "duration_ms": {"type": "integer"}}},
                timeout_s=30, max_retries=2, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:execution.node", name="Node.js Execution",
                category=CapabilityCategory.EXECUTION,
                description="Execute Node.js code in a sandboxed environment",
                risk=CapabilityRisk.MEDIUM,
                input_schema={"required": ["code"], "properties": {"code": {"type": "string"}}},
                output_schema={"required": ["status", "stdout", "exit_code", "duration_ms"],
                               "properties": {"status": {"type": "string"}, "stdout": {"type": "string"},
                                              "exit_code": {"type": "integer"}, "duration_ms": {"type": "integer"}}},
                timeout_s=30, max_retries=2, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:memory.read", name="Memory Read",
                category=CapabilityCategory.MEMORY,
                description="Recall relevant memories from persistent store",
                risk=CapabilityRisk.LOW,
                input_schema={"required": ["query"], "properties": {"query": {"type": "string"}}},
                output_schema={"required": ["memories"], "properties": {"memories": {"type": "array"}}},
                timeout_s=10, max_retries=2, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:memory.write", name="Memory Write",
                category=CapabilityCategory.MEMORY,
                description="Save memories to persistent store",
                risk=CapabilityRisk.MEDIUM,
                input_schema={"required": ["role", "content"], "properties": {
                    "role": {"type": "string"}, "content": {"type": "string"}}},
                output_schema={"required": ["saved"], "properties": {"saved": {"type": "boolean"}}},
                timeout_s=10, max_retries=2, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:search.web", name="Web Search",
                category=CapabilityCategory.SEARCH,
                description="Search the web via Tavily or SearXNG",
                risk=CapabilityRisk.LOW, requires_network=True,
                input_schema={"required": ["query"], "properties": {"query": {"type": "string"}}},
                output_schema={"required": ["results"], "properties": {"results": {"type": "array"}}},
                timeout_s=20, max_retries=2, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:filesystem.read", name="File Read",
                category=CapabilityCategory.FILESYSTEM,
                description="Read a file inside a project directory",
                risk=CapabilityRisk.LOW,
                input_schema={"required": ["path"], "properties": {"path": {"type": "string"}}},
                output_schema={"required": ["path", "content", "size"],
                               "properties": {"path": {"type": "string"}, "content": {"type": "string"},
                                              "size": {"type": "integer"}}},
                timeout_s=5, max_retries=2, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:filesystem.write", name="File Write",
                category=CapabilityCategory.FILESYSTEM,
                description="Write/overwrite a file inside a project directory",
                risk=CapabilityRisk.MEDIUM,
                input_schema={"required": ["path", "content"], "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}}},
                output_schema={"required": ["path", "size", "written_at"],
                               "properties": {"path": {"type": "string"}, "size": {"type": "integer"},
                                              "written_at": {"type": "string"}}},
                timeout_s=10, max_retries=2, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:filesystem.delete", name="File Delete",
                category=CapabilityCategory.FILESYSTEM,
                description="Delete a file from the filesystem",
                risk=CapabilityRisk.HIGH, requires_hitl=True, is_reversible=False,
                input_schema={"required": ["path"], "properties": {"path": {"type": "string"}}},
                output_schema={"required": ["deleted", "path"],
                               "properties": {"deleted": {"type": "boolean"}, "path": {"type": "string"}}},
                timeout_s=5, max_retries=1,
            ),
            CapabilityDescriptor(
                slug="ucip:api.call", name="API Call",
                category=CapabilityCategory.NETWORK,
                description="Make an outbound HTTP API call",
                risk=CapabilityRisk.MEDIUM, requires_network=True,
                input_schema={"required": ["url", "method"], "properties": {
                    "url": {"type": "string"}, "method": {"type": "string"}}},
                output_schema={"required": ["status_code", "body"],
                               "properties": {"status_code": {"type": "integer"}, "body": {"type": "string"}}},
                timeout_s=30, max_retries=2, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:agent.spawn", name="Spawn Agent",
                category=CapabilityCategory.AGENT,
                description="Delegate a sub-task to another Worker persona",
                risk=CapabilityRisk.HIGH, requires_hitl=True, is_reversible=False,
                trust_required="autonomous",
                input_schema={"required": ["worker", "goal"], "properties": {
                    "worker": {"type": "string"}, "goal": {"type": "string"}}},
                output_schema={"required": ["output", "success"],
                               "properties": {"output": {"type": "string"}, "success": {"type": "boolean"}}},
                timeout_s=180, max_retries=1,
            ),
            CapabilityDescriptor(
                slug="ucip:secret.read", name="Secret Read",
                category=CapabilityCategory.SYSTEM,
                description="Read encrypted secrets from the vault",
                risk=CapabilityRisk.HIGH, requires_hitl=True,
                input_schema={"required": ["name"], "properties": {"name": {"type": "string"}}},
                output_schema={"required": ["value"], "properties": {"value": {"type": "string"}}},
                timeout_s=5, max_retries=1, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:system.shell", name="System Shell",
                category=CapabilityCategory.SYSTEM,
                description="Execute a shell command directly (ROOT only)",
                risk=CapabilityRisk.CRITICAL, requires_hitl=True,
                trust_required="root",
                input_schema={"required": ["command"], "properties": {"command": {"type": "string"}}},
                output_schema={"required": ["status", "stdout", "stderr", "exit_code"],
                               "properties": {"status": {"type": "string"}, "stdout": {"type": "string"},
                                              "stderr": {"type": "string"}, "exit_code": {"type": "integer"}}},
                timeout_s=60, max_retries=1, is_reversible=False,
            ),
            CapabilityDescriptor(
                slug="ucip:vcs.write", name="VCS Write",
                category=CapabilityCategory.VCS,
                description="Stage and commit changes in git",
                risk=CapabilityRisk.MEDIUM,
                input_schema={"required": ["message"], "properties": {"message": {"type": "string"}}},
                output_schema={"required": ["success", "stdout", "exit_code"],
                               "properties": {"success": {"type": "boolean"}, "stdout": {"type": "string"},
                                              "exit_code": {"type": "integer"}}},
                timeout_s=30, max_retries=1, is_reversible=True,
            ),
            CapabilityDescriptor(
                slug="ucip:vcs.push", name="VCS Push",
                category=CapabilityCategory.VCS,
                description="Push committed changes to remote",
                risk=CapabilityRisk.HIGH, requires_hitl=True, requires_network=True,
                is_reversible=False,
                input_schema={"required": ["remote", "branch"], "properties": {
                    "remote": {"type": "string"}, "branch": {"type": "string"}}},
                output_schema={"required": ["success", "stdout", "exit_code"],
                               "properties": {"success": {"type": "boolean"}, "stdout": {"type": "string"},
                                              "exit_code": {"type": "integer"}}},
                timeout_s=30, max_retries=1,
            ),
        ]
        for cap in builtins:
            self.register(cap)

    def register(self, descriptor: CapabilityDescriptor) -> CapabilityDescriptor:
        self._registry[descriptor.slug] = descriptor
        logger.info(f"[capabilities] registered: {descriptor.slug} v{descriptor.version}")
        return descriptor

    def get(self, slug: str) -> Optional[CapabilityDescriptor]:
        return self._registry.get(slug)

    def list_all(self, category: Optional[CapabilityCategory] = None) -> list[CapabilityDescriptor]:
        caps = list(self._registry.values())
        if category:
            caps = [c for c in caps if c.category == category]
        return sorted(caps, key=lambda c: c.slug)

    def list_by_risk(self, max_risk: CapabilityRisk) -> list[CapabilityDescriptor]:
        risk_order = {CapabilityRisk.LOW: 0, CapabilityRisk.MEDIUM: 1,
                      CapabilityRisk.HIGH: 2, CapabilityRisk.CRITICAL: 3}
        max_level = risk_order.get(max_risk, 3)
        return [c for c in self._registry.values()
                if risk_order.get(c.risk, 0) <= max_level]

    def list_by_trust(self, trust_level: str) -> list[CapabilityDescriptor]:
        """Capabilities available at a given trust level."""
        trust_order = {"read_only": 0, "assistant": 1, "operator": 2,
                       "autonomous": 3, "root": 4}
        required = trust_order.get(trust_level.lower(), 0)
        return [c for c in self._registry.values()
                if trust_order.get(c.trust_required, 0) <= required]

    def count(self) -> int:
        return len(self._registry)

    def to_dict(self) -> dict:
        return {"capabilities": [c.to_dict() for c in self.list_all()],
                "count": self.count()}

    def categories(self) -> list[dict]:
        cats = {}
        for c in self._registry.values():
            cat = c.category.value
            if cat not in cats:
                cats[cat] = {"category": cat, "count": 0, "risks": set()}
            cats[cat]["count"] += 1
            cats[cat]["risks"].add(c.risk.value)
        return [{"category": v["category"], "count": v["count"],
                 "risks": sorted(v["risks"])} for v in cats.values()]


# Module-level singleton
_registry = CapabilityRegistry()


def get_registry() -> CapabilityRegistry:
    return _registry


def register_capability(descriptor: CapabilityDescriptor) -> CapabilityDescriptor:
    return _registry.register(descriptor)


def list_capabilities(category: Optional[CapabilityCategory] = None) -> list[CapabilityDescriptor]:
    return _registry.list_all(category)