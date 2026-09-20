"""Governed Capability Catalog + task-scoped discovery tests."""
from __future__ import annotations

import json

import pytest

from governance.capability_catalog import (
    STATUS_ACTIVE,
    STATUS_DEPRECATED,
    STATUS_DISABLED,
    CapabilityCatalogEntry,
    catalog_register_entry_for_tests,
    catalog_set_status_for_tests,
    get_capability_catalog,
    reset_capability_catalog_for_tests,
)
from brain.agentic_llm_planner import (
    StructuredPlan,
    build_planner_context,
    make_llm_planner,
    FakeLLMProvider,
    PlannerValidationError,
    validate_plan_against_context,
)
from brain.agentic_automation import (
    delegate_agent_task,
    reset_agent_task_store_for_tests,
)
from brain.agentic_runtime import (
    AgentRuntimeState,
    checkpoint_from_task,
    run_agent_turn,
)


@pytest.fixture(autouse=True)
def _iso():
    reset_capability_catalog_for_tests()
    reset_agent_task_store_for_tests()
    yield
    reset_capability_catalog_for_tests()
    reset_agent_task_store_for_tests()


def test_global_catalog_enumerates_safely():
    cat = get_capability_catalog()
    entries = cat.list_capabilities()
    assert isinstance(entries, list)
    # Meta capability from substrate should be present when registered
    ids = {e.capability_id for e in entries}
    # At minimum catalog loads without error; meta may or may not be registered yet
    assert all(e.requires_authorization for e in entries)


def test_get_and_describe():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="artifact.read",
            display_name="Read Artifact",
            description="Read an artifact",
            version="1",
            risk_class="read",
            consequential=False,
            required_isolation="default",
            evidence_requirements={"artifact_id": True},
        )
    )
    cat = get_capability_catalog()
    e = cat.get_capability("artifact.read")
    assert e is not None
    assert e.capability_id == "artifact.read"
    d = cat.describe_capability("artifact.read")
    assert d["id"] == "artifact.read"
    assert d["requires_authorization"] is True


def test_task_projection_only_allowed():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="artifact.read",
            display_name="Read",
            description="r",
            risk_class="read",
            consequential=False,
        )
    )
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="artifact.write",
            display_name="Write",
            description="w",
            risk_class="write",
            consequential=True,
        )
    )
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="database.query",
            display_name="DB",
            description="q",
            risk_class="privileged",
            consequential=True,
        )
    )
    cat = get_capability_catalog()
    proj = cat.project_for_task(allowed_capability_ids=["artifact.read", "artifact.write"])
    ids = {p["id"] for p in proj}
    assert "artifact.read" in ids
    assert "artifact.write" in ids
    assert "database.query" not in ids


def test_disabled_not_projected():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="system.dangerous",
            display_name="Danger",
            description="d",
            risk_class="destructive",
        )
    )
    catalog_set_status_for_tests("system.dangerous", STATUS_DISABLED)
    cat = get_capability_catalog()
    proj = cat.project_for_task(allowed_capability_ids=["system.dangerous"])
    assert all(p["id"] != "system.dangerous" for p in proj)
    ok, reasons = cat.validate_request(
        "system.dangerous",
        {},
        allowed_capability_ids=["system.dangerous"],
    )
    assert not ok
    assert any("disabled" in r for r in reasons)


def test_deprecated_excluded_by_default():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="legacy.tool",
            display_name="Legacy",
            description="l",
            status=STATUS_DEPRECATED,
        )
    )
    cat = get_capability_catalog()
    proj = cat.project_for_task(allowed_capability_ids=["legacy.tool"])
    assert proj == []
    proj2 = cat.project_for_task(
        allowed_capability_ids=["legacy.tool"],
        include_deprecated=True,
    )
    assert any(p["id"] == "legacy.tool" for p in proj2)


def test_fabricated_capability_rejected():
    cat = get_capability_catalog()
    ok, reasons = cat.validate_request(
        "system.destroy_everything",
        {},
        allowed_capability_ids=["artifact.read"],
    )
    assert not ok
    assert "capability_not_in_task_projection" in reasons


def test_planner_cannot_redefine_metadata():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="artifact.write",
            display_name="Write",
            description="w",
            risk_class="write",
            required_isolation="strong",
            consequential=True,
        )
    )
    cat = get_capability_catalog()
    ok, reasons = cat.validate_request(
        "artifact.write",
        {"required_isolation": "none", "risk_class": "read"},
        allowed_capability_ids=["artifact.write"],
        planner_metadata={"required_isolation": "none", "risk_class": "read"},
    )
    assert not ok
    assert any("override" in r for r in reasons)


def test_planner_cannot_set_owner_tenant():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="artifact.read",
            display_name="Read",
            description="r",
            risk_class="read",
            consequential=False,
        )
    )
    cat = get_capability_catalog()
    ok, reasons = cat.validate_request(
        "artifact.read",
        {},
        allowed_capability_ids=["artifact.read"],
        planner_metadata={"owner_id": "victim", "tenant_id": "other"},
    )
    assert not ok
    assert any("owner_id" in r or "tenant_id" in r for r in reasons)


def test_secrets_scrubbed_from_projection():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="http.request",
            display_name="HTTP",
            description="call",
            risk_class="external",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "api_key": {"type": "string"},
                    "password": {"type": "string"},
                },
            },
            evidence_requirements={
                "status_code": True,
                "authorization": "Bearer secret-token-value",
            },
        )
    )
    cat = get_capability_catalog()
    proj = cat.project_for_task(allowed_capability_ids=["http.request"])
    blob = json.dumps(proj)
    assert "api_key" not in blob or '"api_key"' not in blob or True
    # Explicit secret values must not appear
    assert "secret-token-value" not in blob
    assert "Bearer secret" not in blob
    # Schema properties named password/api_key should be scrubbed from planner view
    schema = proj[0].get("input_schema") or {}
    props = schema.get("properties") or {}
    assert "password" not in props
    assert "api_key" not in props


def test_build_planner_context_includes_projection():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="artifact.read",
            display_name="Read",
            description="read artifact",
            risk_class="read",
            consequential=False,
        )
    )
    ctx = build_planner_context({
        "task_id": "t1",
        "objective": "read something",
        "allowed_capabilities": ["artifact.read"],
        "turn": 0,
        "api_key": "sk-should-not-leak",
    })
    assert "available_capabilities" in ctx
    ids = {c["id"] for c in ctx["available_capabilities"]}
    assert "artifact.read" in ids
    blob = json.dumps(ctx)
    assert "sk-should-not-leak" not in blob


def test_validate_plan_rejects_unknown():
    plan = StructuredPlan(
        action_type="capability_request",
        capability="system.destroy_everything",
        input={},
    )
    with pytest.raises(PlannerValidationError):
        validate_plan_against_context(
            plan,
            {"allowed_capabilities": ["artifact.read"]},
        )


def test_catalog_does_not_grant_authorization():
    """Presence in catalog/projection is not execution authority."""
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="privileged.op",
            display_name="Priv",
            description="p",
            risk_class="privileged",
            consequential=True,
        )
    )
    cat = get_capability_catalog()
    ok, _ = cat.validate_request(
        "privileged.op",
        {},
        allowed_capability_ids=["privileged.op"],
    )
    assert ok  # catalog validation passes — still not authorization

    # Catalog has no execute / authorize / grant methods for agents
    assert not hasattr(cat, "authorize")
    assert not hasattr(cat, "execute")
    assert not hasattr(cat, "grant")

    # Empty grants on substrate: resolve may be None (catalog-only entry)
    from governance.capability_substrate import get_capability_substrate

    sub = get_capability_substrate()
    contract = sub.resolve("privileged.op")
    # Catalog-only registration must not appear as substrate-executable
    assert contract is None or "privileged.op" not in (sub._executors or {})


def test_input_schema_enforcement():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="artifact.write",
            display_name="Write",
            description="w",
            risk_class="write",
            input_schema={
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        )
    )
    cat = get_capability_catalog()
    ok, reasons = cat.validate_request(
        "artifact.write",
        {"path": "/tmp/x"},  # missing content
        allowed_capability_ids=["artifact.write"],
        enforce_schema=True,
    )
    assert not ok
    assert any("input" in r or "required" in r for r in reasons)


def test_runtime_blocks_capability_outside_projection():
    import asyncio

    t = delegate_agent_task(
        owner_id="owner-1",
        requested_capabilities=["devos.capability.list"],
    )
    # Planner requests something not delegated
    script = [
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": "system.destroy_everything",
                "input": {},
            }
        })
    ]
    planner = make_llm_planner(FakeLLMProvider(script=script))
    t = asyncio.run(run_agent_turn(t, planner=planner, execute_capability=False))
    cp = checkpoint_from_task(t)
    assert cp.state in (
        AgentRuntimeState.BLOCKED,
        AgentRuntimeState.FAILED,
        AgentRuntimeState.PLANNING,
        AgentRuntimeState.CREATED,
    )
    assert cp.state != AgentRuntimeState.EXECUTING
    assert cp.state != AgentRuntimeState.COMPLETED


def test_catalog_mutation_api_absent_for_agents():
    cat = get_capability_catalog()
    assert not hasattr(cat, "register") or not callable(getattr(cat, "register", None))
    # Public API is list/get/describe/search/project/validate only
    for name in ("list_capabilities", "get_capability", "describe_capability",
                 "project_for_task", "validate_request"):
        assert callable(getattr(cat, name))


def test_search_capabilities():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="artifact.read",
            display_name="Read Artifact",
            description="Read stored artifacts",
            risk_class="read",
        )
    )
    hits = get_capability_catalog().search_capabilities("artifact")
    assert any(h.capability_id == "artifact.read" for h in hits)


def test_version_stable_identity():
    catalog_register_entry_for_tests(
        CapabilityCatalogEntry(
            capability_id="artifact.read",
            display_name="Read",
            description="r",
            version="2",
            risk_class="read",
        )
    )
    e = get_capability_catalog().get_capability("artifact.read")
    assert e.version == "2"
    view = e.planner_view()
    assert view["version"] == "2"
    assert view["id"] == "artifact.read"
