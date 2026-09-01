"""Test CapabilityRegistry — UCIP capability descriptor registration and query."""
import pytest

from governance.capability_registry import (
    CapabilityDescriptor, CapabilityCategory, CapabilityRisk,
    CapabilityRegistry, get_registry, register_capability, list_capabilities,
)


class TestCapabilityDescriptor:
    def test_creation_defaults(self):
        cap = CapabilityDescriptor(
            slug="test:foo", name="Foo", category=CapabilityCategory.EXECUTION,
            description="Test capability",
        )
        assert cap.slug == "test:foo"
        assert cap.risk == CapabilityRisk.MEDIUM
        assert cap.timeout_s == 30
        assert cap.max_retries == 3
        assert cap.is_reversible is True
        assert cap.requires_hitl is False
        assert cap.version == "1.0.0"

    def test_to_dict(self):
        cap = CapabilityDescriptor(
            slug="test:bar", name="Bar", category=CapabilityCategory.MEMORY,
            description="Memory test", risk=CapabilityRisk.LOW,
        )
        d = cap.to_dict()
        assert d["slug"] == "test:bar"
        assert d["category"] == "memory"
        assert d["risk"] == "low"
        assert d["version"] == "1.0.0"

    def test_sign_and_verify(self):
        cap = CapabilityDescriptor(
            slug="test:signed", name="Signed", category=CapabilityCategory.SYSTEM,
            description="Signed capability",
        )
        assert cap.signature is None
        cap.sign("secret-key")
        assert cap.signature is not None
        assert len(cap.signature) == 64  # SHA-256 hex

        assert cap.verify("secret-key")
        assert not cap.verify("wrong-key")

    def test_verify_without_signature(self):
        cap = CapabilityDescriptor(
            slug="test:nosig", name="NoSig", category=CapabilityCategory.SEARCH,
            description="No signature",
        )
        assert not cap.verify("secret-key")


class TestCapabilityRegistryBootstrap:
    def test_registry_is_singleton(self):
        r1 = CapabilityRegistry()
        r2 = CapabilityRegistry()
        assert r1 is r2

    def test_bootstrap_registers_all_builtins(self):
        registry = get_registry()
        assert registry.count() >= 15  # 15 built-in capabilities

    def test_all_builtins_have_required_fields(self):
        for cap in get_registry().list_all():
            assert cap.slug, f"Missing slug for {cap.name}"
            assert cap.name, f"Missing name for {cap.slug}"
            assert cap.category is not None
            assert cap.description, f"Missing description for {cap.slug}"

    def test_get_known_capability(self):
        cap = get_registry().get("ucip:execution.python")
        assert cap is not None
        assert cap.name == "Python Execution"
        assert cap.category == CapabilityCategory.EXECUTION

    def test_get_unknown_capability(self):
        assert get_registry().get("ucip:does.not.exist") is None


class TestCapabilityRegistryFiltering:
    def test_list_by_category(self):
        caps = get_registry().list_all(category=CapabilityCategory.EXECUTION)
        assert len(caps) >= 3
        for c in caps:
            assert c.category == CapabilityCategory.EXECUTION

    def test_list_by_risk(self):
        caps = get_registry().list_by_risk(CapabilityRisk.LOW)
        for c in caps:
            assert c.risk in (CapabilityRisk.LOW,)

    def test_list_by_trust(self):
        caps = get_registry().list_by_trust("operator")
        assert len(caps) > 0
        # All returned caps should be operator-level or below
        for c in caps:
            assert c.trust_required in ("read_only", "assistant", "operator")

    def test_list_by_trust_root_sees_all(self):
        caps = get_registry().list_by_trust("root")
        assert len(caps) == get_registry().count()

    def test_categories(self):
        cats = get_registry().categories()
        assert len(cats) >= 5
        categories = {c["category"] for c in cats}
        assert "execution" in categories
        assert "memory" in categories


class TestCapabilityRegistryRegister:
    def test_register_new_capability(self):
        cap = CapabilityDescriptor(
            slug="test:custom", name="Custom", category=CapabilityCategory.NETWORK,
            description="Custom test capability",
        )
        result = register_capability(cap)
        assert result is cap
        assert get_registry().get("test:custom") is cap

    def test_register_overwrites_existing(self):
        cap1 = CapabilityDescriptor(
            slug="test:overwrite", name="Original", category=CapabilityCategory.MEMORY,
            description="Original",
        )
        cap2 = CapabilityDescriptor(
            slug="test:overwrite", name="Updated", category=CapabilityCategory.MEMORY,
            description="Updated",
        )
        register_capability(cap1)
        register_capability(cap2)
        assert get_registry().get("test:overwrite").name == "Updated"


class TestListCapabilitiesModuleFunction:
    def test_list_all(self):
        caps = list_capabilities()
        assert len(caps) > 0

    def test_list_by_category(self):
        caps = list_capabilities(category=CapabilityCategory.SYSTEM)
        for c in caps:
            assert c.category == CapabilityCategory.SYSTEM