"""Contract tests for the shared authorization-scope foundation."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.authorization_scopes import (
    AUTHORIZATION_SCOPE_CATALOG,
    PLATFORM_SUPERUSER_SCOPE,
    SOLUTIONS_BUILD_SCOPE,
    is_valid_scope_key,
    validate_catalog,
    validate_role_scopes,
)
from src.core.principal import UserPrincipal
from src.models.contracts.users import RoleCreate


def _principal(*, scopes: list[str] | None = None, is_superuser: bool = False):
    return UserPrincipal(
        user_id=uuid4(),
        email="scope-test@example.com",
        organization_id=uuid4(),
        scopes=scopes or [],
        is_superuser=is_superuser,
    )


def test_catalog_keys_are_unique_and_valid() -> None:
    validate_catalog()
    keys = [scope.key for scope in AUTHORIZATION_SCOPE_CATALOG]
    assert len(keys) == len(set(keys))
    assert all(is_valid_scope_key(key) for key in keys)
    assert is_valid_scope_key("tables.documents.read")
    assert is_valid_scope_key("apps.read.all")
    assert not is_valid_scope_key("Apps.Read.All")
    assert not is_valid_scope_key("solutions.can_build")


def test_custom_role_scopes_are_cataloged_normalized_and_assignable() -> None:
    assert validate_role_scopes(
        [SOLUTIONS_BUILD_SCOPE, SOLUTIONS_BUILD_SCOPE],
        custom_role=True,
    ) == [SOLUTIONS_BUILD_SCOPE]

    with pytest.raises(ValueError, match="Unknown authorization scope"):
        validate_role_scopes(["solutions.fly"], custom_role=True)

    with pytest.raises(ValueError, match="reserved"):
        validate_role_scopes([PLATFORM_SUPERUSER_SCOPE], custom_role=True)


def test_role_contract_rejects_unknown_or_reserved_scopes() -> None:
    with pytest.raises(ValidationError, match="Unknown authorization scope"):
        RoleCreate(name="Invalid", scopes=["solutions.fly"])

    with pytest.raises(ValidationError, match="reserved"):
        RoleCreate(name="Invalid", scopes=[PLATFORM_SUPERUSER_SCOPE])


def test_principal_has_exact_scope_and_compatibility_wildcard() -> None:
    builder = _principal(scopes=[SOLUTIONS_BUILD_SCOPE])
    assert builder.has_scope(SOLUTIONS_BUILD_SCOPE)
    assert not builder.has_scope("roles.manage")

    scope_admin = _principal(scopes=[PLATFORM_SUPERUSER_SCOPE])
    assert scope_admin.has_scope("roles.manage")

    legacy_admin = _principal(is_superuser=True)
    assert legacy_admin.has_scope("roles.manage")
