"""
XP must never become a hidden power level.

Level 20 specialists do not gain filesystem deletion, production deploy,
or any UCIP capability beyond what identity + UCIP already allow.
"""
from brain.persona_xp import (
    XP_AUTHORITY_BOUNDARY,
    calculate_level,
    level_has_no_security_effect,
    profile_to_dict,
    assert_xp_does_not_grant_authority,
)


def test_authority_boundary_text():
    assert "UCIP" in XP_AUTHORITY_BOUNDARY


def test_high_level_has_no_security_effect():
    assert level_has_no_security_effect(1) is True
    assert level_has_no_security_effect(20) is True
    assert level_has_no_security_effect(999) is True
    assert_xp_does_not_grant_authority()


def test_level_math_independent_of_capabilities():
    assert calculate_level(0) == 1
    assert calculate_level(5000) >= 10
    assert level_has_no_security_effect(calculate_level(5000))


def test_profile_dict_denies_security_grants():
    class _P:
        persona_id = "web"
        display_name = "Atlas"
        description = None
        provider = None
        model = None
        xp = 5000
        level = 20
        tasks_completed = 100
        tasks_successful = 90
        tasks_failed = 0
        verified_outcomes = 80
        delegations_received = 10
        delegations_successful = 8
        specialty_xp = {"frontend": 200}
        accomplishments = []
        learning_events = []
        created_at = None
        updated_at = None

    d = profile_to_dict(_P(), {"name": "Web", "specialty": "web", "role": "specialist"})
    assert d["security"]["grants_capabilities"] is False
    assert d["security"]["grants_trust"] is False
    assert d["security"]["bypasses_ucip"] is False
    assert d["security"]["level_is_not_power"] is True
    assert d["level"] == 20


def test_xp_module_has_no_authorization_runtime_deps():
    import brain.persona_xp as mod
    import inspect
    src = inspect.getsource(mod)
    assert "UCIPGateway" not in src
    assert "AgentIdentity(" not in src
