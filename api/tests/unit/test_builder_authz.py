"""Unit tests for the solutions.build capability decision."""

from __future__ import annotations

from uuid import uuid4

from shared.authorization_scopes import SOLUTIONS_BUILD_SCOPE
from src.core.principal import UserPrincipal
from src.services.solutions.builder_authz import can_build


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
