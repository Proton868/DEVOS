"""
Governed Capability Catalog — metadata only.

Catalog describes.
Projection constrains.
Planner proposes.
Runtime validates.
UCIP authorizes.
Operation/Job records.
Isolation constrains.
Evidence proves.

This module does NOT execute capabilities and cannot grant authorization.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Risk vocabulary (descriptive; never authorizes)
RISK_CLASSES = frozenset({
    "read",
    "write",
    "external",
    "privileged",
    "destructive",
    "delegation",
    "low",
    "medium",
    "high",
    "critical",
})

STATUS_ACTIVE = "active"
STATUS_DEPRECATED = "deprecated"
STATUS_DISABLED = "disabled"

_SECRET_KEY_RE = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|credential|private[_-]?key|"
    r"access[_-]?key|jwt|bearer|connection[_-]?string|database_url|dsn)",
    re.I,
)
_SECRET_VALUE_RE = re.compile(
    r"(sk-[a-zA-Z0-9]{8,}|ghp_[a-zA-Z0-9]{8,}|eyJ[a-zA-Z0-9_-]{10,}|"
    r"postgresql://[^\s]+|postgres://[^\s]+|mongodb(\+srv)?://[^\s]+|"
    r"-----BEGIN .*PRIVATE KEY-----)",
    re.I,
)


def _scrub(obj: Any, depth: int = 0) -> Any:
    """Remove secret-bearing keys/values from planner-facing metadata."""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            if _SECRET_KEY_RE.search(ks):
                continue
            if isinstance(v, str) and _SECRET_VALUE_RE.search(v):
                continue
            cleaned = _scrub(v, depth + 1)
            if cleaned is not None:
                out[ks] = cleaned
        return out
    if isinstance(obj, list):
        return [_scrub(x, depth + 1) for x in obj[:50]]
    if isinstance(obj, str):
        if _SECRET_VALUE_RE.search(obj):
            return "[redacted]"
        return obj[:2000]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return str(obj)[:200]


def _map_risk(raw: Any) -> str:
    s = str(raw or "medium").strip().lower()
    if s in RISK_CLASSES:
        return s
    # Map common registry values
    if s in ("info", "none"):
        return "read"
    return "medium"


def _map_status(meta: dict) -> str:
    st = str((meta or {}).get("status") or STATUS_ACTIVE).strip().lower()
    if st in (STATUS_ACTIVE, STATUS_DEPRECATED, STATUS_DISABLED):
        return st
    if (meta or {}).get("disabled") is True:
        return STATUS_DISABLED
    if (meta or {}).get("deprecated") is True:
        return STATUS_DEPRECATED
    return STATUS_ACTIVE


@dataclass(frozen=True)
class CapabilityCatalogEntry:
    """Canonical descriptive capability metadata (non-executable)."""

    capability_id: str
    display_name: str
    description: str
    version: str = "1"
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    risk_class: str = "medium"
    consequential: bool = True
    requires_authorization: bool = True
    required_isolation: str = "default"
    supports_dry_run: bool = False
    evidence_requirements: dict = field(default_factory=dict)
    status: str = STATUS_ACTIVE
    category: str = "general"
    source: str = "registry"
    # Maintenance eligibility (descriptive only; never authorizes)
    maintenance: dict = field(default_factory=dict)

    def planner_view(self) -> dict:
        """Safe structured metadata for planner context only."""
        return {
            "id": self.capability_id,
            "display_name": self.display_name,
            "description": (self.description or "")[:500],
            "version": self.version,
            "input_schema": _scrub(self.input_schema) or {},
            "output_schema": _scrub(self.output_schema) or {},
            "risk_class": self.risk_class,
            "consequential": bool(self.consequential),
            "requires_authorization": True,  # always true for planner; cannot weaken
            "required_isolation": self.required_isolation,
            "supports_dry_run": bool(self.supports_dry_run),
            "evidence_requirements": _scrub(self.evidence_requirements) or {},
            "status": self.status,
            "category": self.category,
            "maintenance": _scrub(self.maintenance) or {},
        }

    def to_dict(self) -> dict:
        return self.planner_view()


def _entry_from_contract(c: Any) -> CapabilityCatalogEntry:
    perms = dict(getattr(c, "permissions", None) or {})
    meta = dict(getattr(c, "metadata", None) or {})
    risk = _map_risk(perms.get("risk") or meta.get("risk"))
    isolation = str(
        perms.get("required_isolation")
        or meta.get("required_isolation")
        or ("network" if perms.get("requires_network") else "default")
    )
    consequential = meta.get("consequential")
    if consequential is None:
        # Reads/meta low-risk may be non-consequential; default true for safety
        consequential = risk not in ("read", "low")
    evidence = dict(getattr(c, "evidence", None) or {})
    if not evidence:
        evidence = dict(meta.get("evidence_requirements") or {})
    return CapabilityCatalogEntry(
        capability_id=str(getattr(c, "identity", "") or ""),
        display_name=str(getattr(c, "name", "") or getattr(c, "identity", "")),
        description=str(getattr(c, "description", "") or ""),
        version=str(getattr(c, "version", "1") or "1"),
        input_schema=dict(getattr(c, "input_schema", None) or {}),
        output_schema=dict(getattr(c, "output_schema", None) or {}),
        risk_class=risk,
        consequential=bool(consequential),
        requires_authorization=True,
        required_isolation=isolation,
        supports_dry_run=bool(meta.get("supports_dry_run", False)),
        evidence_requirements=evidence,
        status=_map_status(meta),
        category=str(getattr(c, "category", "general") or "general"),
        source=str(getattr(c, "source", "registry") or "registry"),
    )


def _entry_from_descriptor(d: Any) -> CapabilityCatalogEntry:
    risk = getattr(d, "risk", None)
    risk_v = risk.value if hasattr(risk, "value") else str(risk or "medium")
    meta = dict(getattr(d, "metadata", None) or {})
    isolation = "network" if getattr(d, "requires_network", False) else "default"
    if meta.get("required_isolation"):
        isolation = str(meta["required_isolation"])
    risk_class = _map_risk(risk_v)
    consequential = meta.get("consequential")
    if consequential is None:
        consequential = risk_class not in ("read", "low")
    return CapabilityCatalogEntry(
        capability_id=str(getattr(d, "slug", "") or ""),
        display_name=str(getattr(d, "name", "") or getattr(d, "slug", "")),
        description=str(getattr(d, "description", "") or ""),
        version=str(getattr(d, "version", "1") or "1"),
        input_schema=dict(getattr(d, "input_schema", None) or {}),
        output_schema=dict(getattr(d, "output_schema", None) or {}),
        risk_class=risk_class,
        consequential=bool(consequential),
        requires_authorization=True,
        required_isolation=isolation,
        supports_dry_run=bool(meta.get("supports_dry_run", False)),
        evidence_requirements=dict(meta.get("evidence_requirements") or {}),
        status=_map_status(meta),
        category=str(
            getattr(getattr(d, "category", None), "value", None)
            or getattr(d, "category", "general")
            or "general"
        ),
        source="registry",
    )


class CapabilityCatalog:
    """
    Read-only catalog facade over CapabilitySubstrate + CapabilityRegistry.

    Agents cannot register/mutate capabilities through this interface.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, CapabilityCatalogEntry] = {}
        # Test-only status overrides (disabled/deprecated) without mutating UCIP
        self._status_overrides: dict[str, str] = {}

    def _load_entries(self) -> dict[str, CapabilityCatalogEntry]:
        entries: dict[str, CapabilityCatalogEntry] = {}
        # Substrate contracts
        try:
            from governance.capability_substrate import get_capability_substrate

            sub = get_capability_substrate()
            for c in sub.list_contracts(include_agent_tools=True):
                e = _entry_from_contract(c)
                if e.capability_id:
                    entries[e.capability_id] = e
        except Exception:
            pass
        # Registry descriptors fill gaps
        try:
            from governance.capability_registry import get_registry

            reg = get_registry()
            for d in reg.list_all():
                e = _entry_from_descriptor(d)
                if e.capability_id and e.capability_id not in entries:
                    entries[e.capability_id] = e
        except Exception:
            pass
        # Explicit overrides (test / admin path only via module helpers)
        entries.update(self._overrides)
        # Status overrides
        for cid, st in self._status_overrides.items():
            if cid in entries:
                base = entries[cid]
                entries[cid] = CapabilityCatalogEntry(
                    capability_id=base.capability_id,
                    display_name=base.display_name,
                    description=base.description,
                    version=base.version,
                    input_schema=dict(base.input_schema),
                    output_schema=dict(base.output_schema),
                    risk_class=base.risk_class,
                    consequential=base.consequential,
                    requires_authorization=True,
                    required_isolation=base.required_isolation,
                    supports_dry_run=base.supports_dry_run,
                    evidence_requirements=dict(base.evidence_requirements),
                    status=st,
                    category=base.category,
                    source=base.source,
                )
        return entries

    def list_capabilities(
        self,
        *,
        include_disabled: bool = False,
        include_deprecated: bool = True,
    ) -> list[CapabilityCatalogEntry]:
        out: list[CapabilityCatalogEntry] = []
        for e in self._load_entries().values():
            if e.status == STATUS_DISABLED and not include_disabled:
                continue
            if e.status == STATUS_DEPRECATED and not include_deprecated:
                continue
            out.append(e)
        out.sort(key=lambda x: x.capability_id)
        return out

    def get_capability(self, capability_id: str) -> Optional[CapabilityCatalogEntry]:
        cid = str(capability_id or "").strip()
        if not cid:
            return None
        # Resolve aliases via substrate if available
        try:
            from governance.capability_substrate import get_capability_substrate

            cid = get_capability_substrate().resolve_id(cid)
        except Exception:
            pass
        return self._load_entries().get(cid)

    def describe_capability(self, capability_id: str) -> Optional[dict]:
        e = self.get_capability(capability_id)
        return e.planner_view() if e else None

    def search_capabilities(self, query: str, *, limit: int = 20) -> list[CapabilityCatalogEntry]:
        q = str(query or "").strip().lower()
        if not q:
            return []
        hits: list[CapabilityCatalogEntry] = []
        for e in self.list_capabilities():
            blob = f"{e.capability_id} {e.display_name} {e.description} {e.category}".lower()
            if q in blob:
                hits.append(e)
            if len(hits) >= limit:
                break
        return hits

    def project_for_task(
        self,
        *,
        allowed_capability_ids: Iterable[str],
        owner_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        include_deprecated: bool = False,
    ) -> list[dict]:
        """
        Task-scoped capability projection for the planner.

        Only capabilities in allowed_capability_ids (after alias resolve) are included.
        Disabled capabilities are never projected as executable.
        Secrets are scrubbed from metadata.
        owner/tenant are NOT taken from planner input — parameters are for audit only.
        """
        allowed: set[str] = set()
        try:
            from governance.capability_substrate import get_capability_substrate

            sub = get_capability_substrate()
            for a in allowed_capability_ids or []:
                allowed.add(sub.resolve_id(str(a)))
        except Exception:
            for a in allowed_capability_ids or []:
                allowed.add(str(a).strip())

        projection: list[dict] = []
        entries = self._load_entries()
        for cid in sorted(allowed):
            e = entries.get(cid)
            if e is None:
                # Unknown to catalog: still project a minimal allowlisted stub so
                # task policy can permit ids that are granted but not yet catalogued.
                # Status is active but requires_authorization remains True.
                # Runtime/UCIP still authorize — catalog does not.
                if cid and cid not in ("*",):
                    projection.append(
                        {
                            "id": cid,
                            "display_name": cid,
                            "description": "",
                            "version": "1",
                            "input_schema": {},
                            "output_schema": {},
                            "risk_class": "medium",
                            "consequential": True,
                            "requires_authorization": True,
                            "required_isolation": "default",
                            "supports_dry_run": False,
                            "evidence_requirements": {},
                            "status": STATUS_ACTIVE,
                            "category": "granted",
                        }
                    )
                continue
            if e.status == STATUS_DISABLED:
                continue
            if e.status == STATUS_DEPRECATED and not include_deprecated:
                continue
            view = e.planner_view()
            # Never leak owner/tenant into planner-facing security fields
            view.pop("owner_id", None)
            view.pop("tenant_id", None)
            projection.append(view)
        # owner_id / tenant_id intentionally unused for authorization
        _ = (owner_id, tenant_id)
        return projection

    def validate_request(
        self,
        capability_id: str,
        inputs: Optional[dict],
        *,
        allowed_capability_ids: Iterable[str],
        planner_metadata: Optional[dict] = None,
        enforce_schema: bool = False,
    ) -> tuple[bool, list[str]]:
        """
        Validate a planner capability request against task projection + canonical metadata.

        Does NOT authorize execution. UCIP remains authoritative.
        Schema enforcement is opt-in (substrate still validates at invoke time).
        """
        reasons: list[str] = []
        cid = str(capability_id or "").strip()
        if not cid:
            return False, ["missing_capability_id"]

        try:
            from governance.capability_substrate import get_capability_substrate

            cid = get_capability_substrate().resolve_id(cid)
        except Exception:
            pass

        allowed = {str(a).strip() for a in (allowed_capability_ids or []) if str(a).strip()}
        # Also resolve allowed aliases
        try:
            from governance.capability_substrate import get_capability_substrate

            sub = get_capability_substrate()
            allowed = {sub.resolve_id(a) for a in allowed}
        except Exception:
            pass

        if cid not in allowed and "*" not in allowed:
            reasons.append("capability_not_in_task_projection")

        entry = self.get_capability(cid)
        if entry is not None:
            if entry.status == STATUS_DISABLED:
                reasons.append("capability_disabled")
            # Planner cannot redefine security metadata
            if planner_metadata:
                for forbidden, expected in (
                    ("required_isolation", entry.required_isolation),
                    ("risk_class", entry.risk_class),
                    ("consequential", entry.consequential),
                    ("requires_authorization", True),
                ):
                    if forbidden in planner_metadata:
                        got = planner_metadata.get(forbidden)
                        if str(got).lower() != str(expected).lower() and got != expected:
                            reasons.append(f"planner_metadata_override_rejected:{forbidden}")
                for banned in ("owner_id", "tenant_id", "granted_capabilities", "isolation"):
                    if banned in planner_metadata:
                        reasons.append(f"planner_metadata_forbidden:{banned}")

            # Optional schema check (substrate remains authoritative at invoke)
            if enforce_schema and entry.input_schema:
                try:
                    from governance.capability_substrate import validate_against_schema

                    ok, schema_errs = validate_against_schema(
                        dict(inputs or {}), entry.input_schema
                    )
                    if not ok:
                        reasons.extend([f"input:{e}" for e in schema_errs[:10]])
                except Exception:
                    pass

        if reasons:
            return False, reasons
        return True, []


_CATALOG: Optional[CapabilityCatalog] = None


def get_capability_catalog() -> CapabilityCatalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = CapabilityCatalog()
    return _CATALOG


def reset_capability_catalog_for_tests() -> None:
    global _CATALOG
    _CATALOG = CapabilityCatalog()


def catalog_set_status_for_tests(capability_id: str, status: str) -> None:
    """Test helper: override status without mutating UCIP registry."""
    cat = get_capability_catalog()
    cat._status_overrides[str(capability_id)] = str(status)


def catalog_register_entry_for_tests(entry: CapabilityCatalogEntry) -> None:
    """Test helper: inject catalog entry (not available to agents)."""
    cat = get_capability_catalog()
    cat._overrides[entry.capability_id] = entry
