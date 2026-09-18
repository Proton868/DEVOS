"""Hegemon > Elder > Member platform authorization hierarchy."""
from types import SimpleNamespace

import pytest

from governance.platform_roles import (
    effective_platform_role,
    is_hegemon,
    is_elder_or_above,
    can_administer_platform,
    can_mutate_target_role,
    has_at_least,
    require_at_least,
    public_role_label,
    sync_is_admin_flag,
)


def U(**kw):
    defaults = dict(role="member", is_admin=False, id="u1")
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_hierarchy_ranks():
    assert is_hegemon(U(role="hegemon"))
    assert is_elder_or_above(U(role="elder"))
    assert is_elder_or_above(U(role="hegemon"))
    assert not is_elder_or_above(U(role="member"))
    assert has_at_least(U(role="hegemon"), "elder")
    assert not has_at_least(U(role="member"), "elder")


def test_legacy_is_admin_maps_to_elder_not_hegemon():
    u = U(role="", is_admin=True)
    assert effective_platform_role(u) == "elder"
    assert not is_hegemon(u)
    assert can_administer_platform(u)


def test_member_denied_admin():
    u = U(role="member", is_admin=False)
    assert not can_administer_platform(u)
    with pytest.raises(PermissionError):
        require_at_least(u, "elder")


def test_elder_cannot_mutate_hegemon():
    elder = U(role="elder", is_admin=True)
    hegemon = U(role="hegemon", is_admin=True)
    member = U(role="member")
    assert not can_mutate_target_role(elder, hegemon)
    assert not can_mutate_target_role(elder, hegemon, "member")
    assert not can_mutate_target_role(elder, member, "hegemon")
    assert can_mutate_target_role(elder, member, "member")


def test_hegemon_can_manage_elder_and_member():
    hegemon = U(role="hegemon")
    elder = U(role="elder")
    member = U(role="member")
    assert can_mutate_target_role(hegemon, elder, "member")
    assert can_mutate_target_role(hegemon, member, "elder")
    assert not can_mutate_target_role(member, elder)


def test_public_role_label_and_sync_admin():
    assert public_role_label(U(role="Hegemon")) == "hegemon"
    assert public_role_label(U(role="Elder")) == "elder"
    assert sync_is_admin_flag(U(role="elder")) is True
    assert sync_is_admin_flag(U(role="member")) is False


def test_reject_client_authority_still_strips_role():
    from governance.identity_contract import reject_client_authority_fields
    cleaned = reject_client_authority_fields({"role": "hegemon", "is_admin": True, "title": "x"})
    assert "role" not in cleaned
    assert "is_admin" not in cleaned
    assert cleaned.get("title") == "x"
