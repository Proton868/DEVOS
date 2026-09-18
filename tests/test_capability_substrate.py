"""Tests for the unified capability / execution substrate."""
from __future__ import annotations

import asyncio
import pytest

from governance.capability_substrate import (
    CapabilityContract,
    CapabilitySubstrate,
    InvocationContext,
    InvocationRequest,
    InvocationStatus,
    contract_from_descriptor,
    get_capability_substrate,
    reset_capability_substrate_for_tests,
    validate_against_schema,
    ensure_meta_contracts_registered,
)


@pytest.fixture()
def substrate() -> CapabilitySubstrate:
    ensure_meta_contracts_registered()
    return reset_capability_substrate_for_tests()


def test_validate_required_and_types():
    schema = {
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "n": {"type": "integer"},
        },
    }
    ok, errs = validate_against_schema({}, schema)
    assert not ok
    assert any("path" in e for e in errs)

    ok, errs = validate_against_schema({"path": "/x", "n": 1}, schema)
    assert ok and not errs

    ok, errs = validate_against_schema({"path": "/x", "n": "nope"}, schema)
    assert not ok


def test_contract_from_registry_descriptor(substrate: CapabilitySubstrate):
    from governance.capability_registry import get_registry

    reg = get_registry()
    desc = reg.get("ucip:filesystem.read") or reg.list_all()[0]
    c = contract_from_descriptor(desc)
    assert isinstance(c, CapabilityContract)
    assert c.identity
    assert c.version
    assert "trust_required" in c.permissions
    assert c.timeout_s > 0
    assert c.failure.auth_deny == "fail_closed"


def test_resolve_registry_capability(substrate: CapabilitySubstrate):
    c = substrate.resolve("ucip:filesystem.read")
    assert c is not None
    assert c.identity == "ucip:filesystem.read"
    assert c.source == "registry"


def test_list_contracts_includes_registry(substrate: CapabilitySubstrate):
    contracts = substrate.list_contracts(include_agent_tools=False)
    assert len(contracts) >= 1
    ids = {c.identity for c in contracts}
    assert any(i.startswith("ucip:") for i in ids)


def test_authorize_deny_without_grants(substrate: CapabilitySubstrate):
    c = substrate.resolve("ucip:filesystem.read")
    assert c is not None
    ctx = InvocationContext(
        tenant_id="t1",
        owner_id="u1",
        granted_capabilities=set(),
        actor_type="user",
        actor_id="u1",
        surface="ide",
    )
    ok, reason = substrate.authorize(c, ctx)
    assert ok is False
    assert reason


def test_invoke_denied_without_grants(substrate: CapabilitySubstrate):
    async def _run():
        req = InvocationRequest(
            capability_id="ucip:filesystem.read",
            inputs={},
            context=InvocationContext(
                tenant_id="t1",
                owner_id="u1",
                granted_capabilities=set(),
                surface="nuha",
                actor_type="nuha",
                actor_id="nuha",
            ),
            dry_run=True,
        )
        return await substrate.invoke(req)

    result = asyncio.run(_run())
    assert result.status == InvocationStatus.DENIED
    assert result.authorized is False
    assert result.error_class == "auth"


def test_client_supplied_grants_rejected(substrate: CapabilitySubstrate):
    async def _run():
        req = InvocationRequest(
            capability_id="ucip:filesystem.write",
            inputs={"path": "x"},
            context=InvocationContext(
                tenant_id="t1",
                owner_id="u1",
                granted_capabilities={"ucip:filesystem.write", "*"},
                client_supplied_grants=True,
                surface="nuha",
                actor_type="nuha",
                actor_id="nuha",
            ),
            dry_run=True,
        )
        return await substrate.invoke(req)

    result = asyncio.run(_run())
    assert result.status == InvocationStatus.DENIED
    assert result.auth_reason == "client_supplied_grants_rejected"


def test_dry_run_authorized(substrate: CapabilitySubstrate):
    ensure_meta_contracts_registered()

    async def _run():
        req = InvocationRequest(
            capability_id="devos.capability.list",
            inputs={},
            context=InvocationContext(
                tenant_id="t1",
                owner_id="u1",
                granted_capabilities={"devos.capability.list"},
                surface="test",
                actor_type="test",
                actor_id="test",
            ),
            dry_run=True,
        )
        return await substrate.invoke(req)

    result = asyncio.run(_run())
    assert result.status == InvocationStatus.AUTHORIZED
    assert result.authorized is True
    assert result.outputs and result.outputs.get("dry_run") is True


def test_execute_meta_list(substrate: CapabilitySubstrate):
    ensure_meta_contracts_registered()

    async def _run():
        req = InvocationRequest(
            capability_id="devos.capability.list",
            inputs={},
            context=InvocationContext(
                tenant_id="t1",
                owner_id="u1",
                granted_capabilities={"*"},
                surface="test",
                actor_type="test",
                actor_id="test",
            ),
        )
        return await substrate.invoke(req)

    result = asyncio.run(_run())
    assert result.status == InvocationStatus.EXECUTED
    assert result.outputs is not None
    assert result.outputs.get("count", 0) >= 1


def test_validation_failure(substrate: CapabilitySubstrate):
    ensure_meta_contracts_registered()

    async def _run():
        req = InvocationRequest(
            capability_id="devos.capability.resolve",
            inputs={},
            context=InvocationContext(
                tenant_id="t1",
                owner_id="u1",
                granted_capabilities={"devos.capability.resolve"},
                surface="test",
                actor_type="test",
                actor_id="test",
            ),
        )
        return await substrate.invoke(req)

    result = asyncio.run(_run())
    assert result.status == InvocationStatus.VALIDATION_FAILED
    assert result.authorized is True
    assert result.error_class == "validation"


def test_resolve_meta_executor(substrate: CapabilitySubstrate):
    ensure_meta_contracts_registered()

    async def _run():
        req = InvocationRequest(
            capability_id="devos.capability.resolve",
            inputs={"capability_id": "ucip:filesystem.read"},
            context=InvocationContext(
                tenant_id="t1",
                owner_id="u1",
                granted_capabilities={"*"},
                surface="flow",
                actor_type="user",
                actor_id="u1",
            ),
        )
        return await substrate.invoke(req)

    result = asyncio.run(_run())
    assert result.status == InvocationStatus.EXECUTED
    assert result.outputs and result.outputs.get("found") is True


def test_unknown_capability(substrate: CapabilitySubstrate):
    async def _run():
        req = InvocationRequest(
            capability_id="devos.does.not.exist.ever",
            inputs={},
            context=InvocationContext(
                tenant_id="t1",
                owner_id="u1",
                granted_capabilities={"*"},
                surface="test",
                actor_type="test",
                actor_id="test",
            ),
        )
        return await substrate.invoke(req)

    result = asyncio.run(_run())
    assert result.status == InvocationStatus.FAILED
    assert result.error_class == "not_found"


def test_custom_executor_only_after_auth(substrate: CapabilitySubstrate):
    called = {"n": 0}

    async def boom(contract, req):
        called["n"] += 1
        return {"ok": True}

    substrate.register_executor("ucip:filesystem.read", boom)

    async def _run():
        r1 = await substrate.invoke(
            InvocationRequest(
                capability_id="ucip:filesystem.read",
                inputs={"path": "/tmp"},
                context=InvocationContext(
                    tenant_id="t1",
                    owner_id="u1",
                    granted_capabilities=set(),
                    surface="ide",
                    actor_type="user",
                    actor_id="u1",
                ),
            )
        )
        r2 = await substrate.invoke(
            InvocationRequest(
                capability_id="ucip:filesystem.read",
                inputs={"path": "/tmp"},
                context=InvocationContext(
                    tenant_id="t1",
                    owner_id="u1",
                    granted_capabilities={"ucip:filesystem.read"},
                    surface="ide",
                    actor_type="user",
                    actor_id="u1",
                ),
            )
        )
        return r1, r2

    r1, r2 = asyncio.run(_run())
    assert r1.status == InvocationStatus.DENIED
    assert r1.authorized is False
    assert r2.status == InvocationStatus.EXECUTED
    assert r2.authorized is True
    # Executor must run exactly once (authorized path only).
    assert called["n"] == 1, (r1.to_dict(), r2.to_dict(), called)


def test_star_grant_allows(substrate: CapabilitySubstrate):
    async def _run():
        req = InvocationRequest(
            capability_id="ucip:memory.read",
            inputs={},
            context=InvocationContext(
                tenant_id="t1",
                owner_id="u1",
                granted_capabilities={"*"},
                surface="automation",
                actor_type="system",
                actor_id="worker",
            ),
            dry_run=True,
        )
        return await substrate.invoke(req)

    result = asyncio.run(_run())
    assert result.status in (
        InvocationStatus.AUTHORIZED,
        InvocationStatus.VALIDATION_FAILED,
        InvocationStatus.NOT_IMPLEMENTED,
    )
    assert result.authorized is True


def test_get_singleton():
    a = get_capability_substrate()
    b = get_capability_substrate()
    assert a is b


def test_agent_tool_contract_mapping():
    try:
        from brain.agent_tools import AGENT_TOOL_REGISTRY
    except Exception:
        pytest.skip("agent tools unavailable")
    if not AGENT_TOOL_REGISTRY:
        pytest.skip("no agent tools registered")
    from governance.capability_substrate import contract_from_agent_tool

    tool = AGENT_TOOL_REGISTRY.get("list_files") or next(iter(AGENT_TOOL_REGISTRY.values()))
    c = contract_from_agent_tool(tool)
    assert c.source == "agent_tool"
    assert c.identity
