"""
Enterprise — Capability Marketplace (Agency OS Master Plan §7).

A registry where users can publish, discover, and install custom capabilities.
Capabilities are versioned, tagged, and have trust requirements. The marketplace
is federated — tenants can host their own private marketplace or connect to
the global DevOS marketplace.

Each capability has:
- A manifest (slug, name, version, description, tags)
- Trust requirements (minimum tier, required tokens)
- Pricing (free, one-time, subscription)
- A runtime implementation (Python function, Docker container, or API endpoint)
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from governance.identity_context import TenantTier

logger = logging.getLogger("devos.marketplace")

MARKETPLACE_DIR = Path("data/marketplace")
MARKETPLACE_DIR.mkdir(parents=True, exist_ok=True)


class CapabilityType(str, Enum):
    FUNCTION = "function"     # Python function
    DOCKER = "docker"         # Docker container
    API = "api"               # External API endpoint
    WORKFLOW = "workflow"     # Pre-built workflow


class PricingModel(str, Enum):
    FREE = "free"
    ONE_TIME = "one_time"
    SUBSCRIPTION = "subscription"
    USAGE_BASED = "usage_based"


@dataclass
class MarketplaceEntry:
    """A capability listing in the marketplace."""
    slug: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    category: str = "uncategorized"
    tags: list[str] = field(default_factory=list)
    capability_type: CapabilityType = CapabilityType.FUNCTION
    min_tier: TenantTier = TenantTier.TENANT_USER
    pricing: PricingModel = PricingModel.FREE
    price: float = 0.0
    currency: str = "usd"
    downloads: int = 0
    rating: float = 0.0
    reviews: int = 0
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    icon: str = "🧩"
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "tags": self.tags,
            "capability_type": self.capability_type.value,
            "min_tier": self.min_tier.value,
            "pricing": self.pricing.value,
            "price": self.price,
            "currency": self.currency,
            "downloads": self.downloads,
            "rating": self.rating,
            "reviews": self.reviews,
            "published_at": self.published_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "icon": self.icon,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MarketplaceEntry":
        return cls(
            slug=data["slug"],
            name=data["name"],
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            category=data.get("category", "uncategorized"),
            tags=data.get("tags", []),
            capability_type=CapabilityType(data.get("capability_type", "function")),
            min_tier=TenantTier(data.get("min_tier", "tenant_user")),
            pricing=PricingModel(data.get("pricing", "free")),
            price=float(data.get("price", 0)),
            currency=data.get("currency", "usd"),
            icon=data.get("icon", "🧩"),
            metadata=data.get("metadata", {}),
        )


class CapabilityMarketplace:
    """Federated capability marketplace.

    In Micro profile: local-only, file-backed.
    In Standard profile: Supabase-backed, single-tenant.
    In Enterprise profile: Supabase-backed, multi-tenant, federated.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._entries: dict[str, MarketplaceEntry] = {}
        self._load_from_disk()
        self._bootstrap()
        self._initialized = True
        logger.info("[marketplace] initialized")

    def _load_from_disk(self):
        """Load marketplace entries from disk."""
        index_file = MARKETPLACE_DIR / "index.json"
        if index_file.exists():
            try:
                data = json.loads(index_file.read_text())
                for entry_data in data.get("entries", []):
                    entry = MarketplaceEntry.from_dict(entry_data)
                    self._entries[entry.slug] = entry
                logger.info(f"[marketplace] loaded {len(self._entries)} entries")
            except Exception as e:
                logger.warning(f"[marketplace] load failed: {e}")

    def _save_to_disk(self):
        """Persist marketplace entries to disk."""
        index_file = MARKETPLACE_DIR / "index.json"
        data = {
            "entries": [e.to_dict() for e in self._entries.values()],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        index_file.write_text(json.dumps(data, indent=2))

    def _bootstrap(self):
        """Seed built-in marketplace entries."""
        builtins = [
            MarketplaceEntry(
                slug="devos.code_review",
                name="Code Review Agent",
                version="1.0.0",
                description="Automated code review with best practices, security, and style checks.",
                author="DevOS",
                category="development",
                tags=["code", "review", "quality"],
                capability_type=CapabilityType.FUNCTION,
                min_tier=TenantTier.TENANT_USER,
                pricing=PricingModel.FREE,
                icon="🔍",
            ),
            MarketplaceEntry(
                slug="devos.doc_generator",
                name="Documentation Generator",
                version="1.0.0",
                description="Auto-generate API docs, READMEs, and inline documentation.",
                author="DevOS",
                category="documentation",
                tags=["docs", "documentation", "readme"],
                capability_type=CapabilityType.FUNCTION,
                min_tier=TenantTier.TENANT_USER,
                pricing=PricingModel.FREE,
                icon="📝",
            ),
            MarketplaceEntry(
                slug="devos.test_generator",
                name="Test Generator",
                version="1.0.0",
                description="Generate unit, integration, and E2E tests from code.",
                author="DevOS",
                category="testing",
                tags=["testing", "unit-test", "integration"],
                capability_type=CapabilityType.FUNCTION,
                min_tier=TenantTier.TENANT_USER,
                pricing=PricingModel.FREE,
                icon="🧪",
            ),
            MarketplaceEntry(
                slug="devos.deploy_pipeline",
                name="Deploy Pipeline",
                version="1.0.0",
                description="One-click deploy to Vercel, Netlify, Railway, or AWS.",
                author="DevOS",
                category="deployment",
                tags=["deploy", "ci-cd", "cloud"],
                capability_type=CapabilityType.WORKFLOW,
                min_tier=TenantTier.TENANT_ADMIN,
                pricing=PricingModel.FREE,
                icon="🚀",
            ),
            MarketplaceEntry(
                slug="devos.data_analyzer",
                name="Data Analyzer",
                version="1.0.0",
                description="Analyze CSV, JSON, SQL data with visualizations and insights.",
                author="DevOS",
                category="data",
                tags=["data", "analytics", "visualization"],
                capability_type=CapabilityType.FUNCTION,
                min_tier=TenantTier.TENANT_USER,
                pricing=PricingModel.FREE,
                icon="📊",
            ),
        ]
        for entry in builtins:
            if entry.slug not in self._entries:
                self._entries[entry.slug] = entry
        self._save_to_disk()

    def publish(self, entry: MarketplaceEntry) -> MarketplaceEntry:
        """Publish a capability to the marketplace."""
        entry.updated_at = datetime.now(timezone.utc)
        self._entries[entry.slug] = entry
        self._save_to_disk()
        logger.info(f"[marketplace] published: {entry.slug} v{entry.version}")
        return entry

    def get(self, slug: str) -> Optional[MarketplaceEntry]:
        """Get a marketplace entry by slug."""
        return self._entries.get(slug)

    def list(self, category: Optional[str] = None,
             tag: Optional[str] = None,
             pricing: Optional[PricingModel] = None,
             min_tier: Optional[TenantTier] = None,
             search: Optional[str] = None,
             sort_by: str = "downloads",
             limit: int = 50) -> list[MarketplaceEntry]:
        """List marketplace entries with filtering."""
        results = list(self._entries.values())

        if category:
            results = [e for e in results if e.category == category]
        if tag:
            results = [e for e in results if tag in e.tags]
        if pricing:
            results = [e for e in results if e.pricing == pricing]
        if min_tier:
            from governance.rbac import RBACEngine
            results = [
                e for e in results
                if RBACEngine._tier_rank(e.min_tier) <= RBACEngine._tier_rank(min_tier)
            ]
        if search:
            q = search.lower()
            results = [
                e for e in results
                if q in e.name.lower() or q in e.description.lower()
                or any(q in t.lower() for t in e.tags)
            ]

        if sort_by == "downloads":
            results.sort(key=lambda e: e.downloads, reverse=True)
        elif sort_by == "rating":
            results.sort(key=lambda e: e.rating, reverse=True)
        elif sort_by == "newest":
            results.sort(key=lambda e: e.published_at, reverse=True)
        elif sort_by == "name":
            results.sort(key=lambda e: e.name.lower())

        return results[:limit]

    def categories(self) -> list[str]:
        """List all categories."""
        return sorted(set(e.category for e in self._entries.values()))

    def record_download(self, slug: str):
        """Record a download for a capability."""
        entry = self._entries.get(slug)
        if entry:
            entry.downloads += 1
            self._save_to_disk()

    def delete(self, slug: str) -> bool:
        """Remove a capability from the marketplace."""
        if slug in self._entries:
            del self._entries[slug]
            self._save_to_disk()
            return True
        return False

    def count(self) -> int:
        return len(self._entries)


def get_marketplace() -> CapabilityMarketplace:
    return CapabilityMarketplace()