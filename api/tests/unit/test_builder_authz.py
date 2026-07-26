"""Unit tests for the solutions.build capability decision."""

from __future__ import annotations

from src.services.solutions.builder_authz import (
    SOLUTIONS_BUILD_PERMISSION,
    can_build,
)


def test_role_grant_allows():
    assert can_build(
        is_platform_admin=False,
        is_external=False,
        role_permissions=[{}, {SOLUTIONS_BUILD_PERMISSION: True}],
    )


def test_no_grant_denies():
    assert not can_build(
        is_platform_admin=False,
        is_external=False,
        role_permissions=[{}, {"can_promote_agent": True}],
    )


def test_falsy_grant_value_denies():
    assert not can_build(
        is_platform_admin=False,
        is_external=False,
        role_permissions=[{SOLUTIONS_BUILD_PERMISSION: False}],
    )


def test_platform_admin_bypasses_roles():
    assert can_build(is_platform_admin=True, is_external=False, role_permissions=[])


def test_external_denied_even_with_role_grant():
    assert not can_build(
        is_platform_admin=False,
        is_external=True,
        role_permissions=[{SOLUTIONS_BUILD_PERMISSION: True}],
    )


def test_external_denied_even_with_admin_flag():
    assert not can_build(
        is_platform_admin=True,
        is_external=True,
        role_permissions=[{SOLUTIONS_BUILD_PERMISSION: True}],
    )


def test_no_roles_denies():
    assert not can_build(is_platform_admin=False, is_external=False, role_permissions=[])
