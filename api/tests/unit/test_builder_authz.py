"""Unit tests for the solutions.build capability decision."""

from __future__ import annotations

from uuid import uuid4

from shared.authorization_scopes import (
    ORGANIZATION_IMPERSONATION_SCOPE,
    SOLUTIONS_BUILD_SCOPE,
)
from src.core.principal import UserPrincipal
from src.services.solutions.builder_authz import can_build, can_support_builds


def make_user_principal(**overrides) -> UserPrincipal:
    values = {
        "user_id": uuid4(),
        "email": "builder@example.com",
        "organization_id": uuid4(),
    }
    values.update(overrides)
    return UserPrincipal(**values)


def test_role_grant_allows():
    assert can_build(make_user_principal(scopes=[SOLUTIONS_BUILD_SCOPE]))


def test_no_grant_denies():
    assert not can_build(make_user_principal())


def test_platform_admin_wildcard_allows():
    assert can_build(make_user_principal(is_superuser=True))


def test_external_denied_even_with_role_grant():
    assert not can_build(
        make_user_principal(
            is_external=True,
            scopes=[SOLUTIONS_BUILD_SCOPE],
        )
    )


def test_external_denied_even_with_admin_flag():
    assert not can_build(
        make_user_principal(
            is_superuser=True,
            is_external=True,
        )
    )


def test_provider_operator_can_enter_support_view():
    assert can_support_builds(
        make_user_principal(
            is_provider_org=True,
            scopes=[ORGANIZATION_IMPERSONATION_SCOPE],
        )
    )


def test_impersonation_scope_does_not_turn_customer_into_provider():
    assert not can_support_builds(
        make_user_principal(scopes=[ORGANIZATION_IMPERSONATION_SCOPE])
    )


def test_platform_admin_can_enter_support_view():
    assert can_support_builds(make_user_principal(is_superuser=True))
