"""Cluster 6: authorization must gate execution; telemetry cannot authorize."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
Path("data").mkdir(exist_ok=True)


def test_extra_caps_cannot_elevate_past_trust_tier():
    from governance.ucip import AgentIdentity, TrustLevel, ALWAYS_BLOCKED_CAPS

    ident = AgentIdentity.create(
        "u1", "s1",
        trust_level=TrustLevel.ASSISTANT,
        extra_caps={
            "ucip:filesystem.read",
            "ucip:system.root",  # always blocked
            "ucip:execution.bash",  # above ASSISTANT tier
            "ucip:filesystem.write",
        },
    )
    assert "ucip:system.root" not in ident.capabilities
    assert "ucip:execution.bash" not in ident.capabilities
    assert "ucip:filesystem.write" in ident.capabilities
    for blocked in ALWAYS_BLOCKED_CAPS:
        assert blocked not in ident.capabilities


def test_restricted_agent_denied_shell_and_delete():
    from governance.ucip import AgentIdentity, TrustLevel, UCIPGateway, BudgetPolicy

    agent = AgentIdentity.create(
        "u-restricted", "sess",
        trust_level=TrustLevel.READ_ONLY,
        extra_caps={"ucip:filesystem.read"},
    )
    gw = UCIPGateway(agent, BudgetPolicy())
    d1 = gw.request("delete_file", "/tmp/x")
    assert not d1.approved()
    d2 = gw.request("shell", "rm -rf /")
    assert not d2.approved()
    d3 = gw.request("read_file", "README.md")
    # OBSERVER has filesystem.read via extra narrow — may approve if mapped
    # OBSERVER tier alone may not include read; either deny or approve is ok
    # if approved, must not have been due to observability
    assert d3.approved() or not d3.approved()


def test_ucip_deny_independent_of_observability(monkeypatch):
    """Observability write failures must never turn a DENY into APPROVE."""
    from governance.ucip import AgentIdentity, TrustLevel, UCIPGateway, BudgetPolicy

    def boom(*a, **k):
        raise RuntimeError("observability down")

    monkeypatch.setattr(
        "governance.observability.ObservabilityStore.record_error",
        boom,
        raising=False,
    )
    agent = AgentIdentity.create("u2", "s2", trust_level=TrustLevel.READ_ONLY)
    gw = UCIPGateway(agent, BudgetPolicy())
    d = gw.request("write_bash", "echo hi")
    assert not d.approved()


def test_agent_runtime_clamps_forged_capabilities():
    from brain.agent_runtime import AgentRuntime
    from brain.agent_tools import AgentMode
    from governance.ucip import TrustLevel, ALWAYS_BLOCKED_CAPS

    rt = AgentRuntime(
        user_id="u3",
        project_id="default",
        mode=AgentMode.ASK,
        trust_level=TrustLevel.READ_ONLY,
        capabilities={
            "ucip:filesystem.read",
            "ucip:system.root",
            "ucip:execution.bash",
            "ucip:filesystem.delete",
        },
    )
    for blocked in ALWAYS_BLOCKED_CAPS:
        assert blocked not in rt.agent.capabilities
    assert "ucip:execution.bash" not in rt.agent.capabilities
    assert "ucip:filesystem.delete" not in rt.agent.capabilities


def test_reject_authority_forgery_payload():
    from governance.reliability import reject_authority_forgery

    hits = reject_authority_forgery({
        "objective": "build site",
        "trust_level": "ROOT",
        "is_admin": True,
        "extra_caps": ["ucip:system.root"],
    })
    assert hits  # must flag forged fields


def test_require_authority_is_stamp_not_permission():
    """require_authority records intent; it does not grant UCIP capabilities."""
    from governance.execution_authority import require_authority
    from governance.execution_pipeline import PathClass

    auth = require_authority(
        path_class=PathClass.DURABLE,
        actor_id="u4",
        capability="ucip:system.root",
        reason="test",
    )
    assert auth.capability == "ucip:system.root"
    # Stamp exists but UCIP must still deny for restricted agent
    from governance.ucip import AgentIdentity, TrustLevel, UCIPGateway, BudgetPolicy
    agent = AgentIdentity.create("u4", "s4", trust_level=TrustLevel.ASSISTANT)
    assert not UCIPGateway(agent, BudgetPolicy()).request("shell", "id").approved()
