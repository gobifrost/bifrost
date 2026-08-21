"""Contract tests for the shared authorization-scope foundation."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.authorization_scopes import (
    AUTHORIZATION_SCOPE_CATALOG,
    BUILDER_EXECUTE_SCOPE,
    PLATFORM_SUPERUSER_SCOPE,
    implied_scopes,
    is_valid_scope_key,
    validate_catalog,
    validate_role_scopes,
)
from shared.authorization_defaults_v1 import (
    BUILDER_CAPABILITIES,
    DEFAULT_ROLES_V1,
    DIRECT_WORKSPACE_CAPABILITIES,
    ORGANIZATION_MEMBER_CAPABILITIES,
    PLATFORM_BUILDER_CAPABILITIES,
    PLATFORM_OPERATOR_CAPABILITIES,
)
from src.core.principal import UserPrincipal
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationContext,
)
from src.services.role_assignments import infer_legacy_role_assignment_boundaries
from src.models.contracts.users import (
    AssignUsersToRoleRequest,
    BulkUserOperation,
    RoleAssignmentBoundaryInput,
    RoleAssignmentSelection,
    RoleCreate,
    RolePublic,
    RoleUpdate,
)


def _principal(*, scopes: list[str] | None = None, is_superuser: bool = False):
    return UserPrincipal(
        user_id=uuid4(),
        email="scope-test@example.com",
        organization_id=uuid4(),
        scopes=scopes or [],
        is_superuser=is_superuser,
    )


def _authorization_context(
    *,
    boundary: AuthorizationBoundary,
    principal: UserPrincipal | None = None,
) -> AuthorizationContext:
    requester = principal or _principal()
    return AuthorizationContext(
        requester=requester,
        effective_actor=requester,
        selected_boundary=boundary,
        effective_capabilities=frozenset(requester.scopes),
        grant_sources=(),
    )


def test_catalog_keys_are_unique_and_valid() -> None:
    validate_catalog()
    keys = [scope.key for scope in AUTHORIZATION_SCOPE_CATALOG]
    assert len(keys) == len(set(keys))
    assert all(is_valid_scope_key(key) for key in keys)
    assert is_valid_scope_key("tabledocuments.read")
    assert is_valid_scope_key("solutions.publish.execute")
    assert not is_valid_scope_key("Apps.Read.All")
    assert not is_valid_scope_key("apps.read.all")
    assert not is_valid_scope_key("apps.write")
    assert not is_valid_scope_key("apps.publish")
    assert not is_valid_scope_key("solutions.can_build")


def test_custom_role_scopes_are_cataloged_normalized_and_assignable() -> None:
    assert validate_role_scopes(
        [BUILDER_EXECUTE_SCOPE, BUILDER_EXECUTE_SCOPE],
        custom_role=True,
    ) == [BUILDER_EXECUTE_SCOPE]

    with pytest.raises(ValueError, match="Unknown authorization scope"):
        validate_role_scopes(["solutions.fly"], custom_role=True)

    with pytest.raises(ValueError, match="reserved"):
        validate_role_scopes([PLATFORM_SUPERUSER_SCOPE], custom_role=True)


def test_role_contract_rejects_unknown_or_reserved_scopes() -> None:
    with pytest.raises(ValidationError, match="Unknown authorization scope"):
        RoleCreate(name="Invalid", capabilities=["solutions.fly"])

    with pytest.raises(ValidationError, match="reserved"):
        RoleCreate(name="Invalid", capabilities=[PLATFORM_SUPERUSER_SCOPE])


def test_role_contract_accepts_legacy_scopes_as_capabilities() -> None:
    created = RoleCreate.model_validate(
        {"name": "Builder", "scopes": ["solutions.build", "agents.write"]}
    )
    updated = RoleUpdate.model_validate({"scopes": ["workflows.write"]})

    assert created.capabilities == [
        "agents.readwrite",
        "builder.execute",
        "solutions.build.execute",
        "solutions.deploy.execute",
        "solutions.readwrite",
    ]
    assert updated.capabilities == ["workflows.readwrite"]


def test_role_contract_accepts_empty_legacy_permissions() -> None:
    created = RoleCreate.model_validate({"name": "Empty", "permissions": {}})
    updated = RoleUpdate.model_validate({"permissions": {}})

    assert created.capabilities == []
    assert updated.capabilities is None


def test_role_contract_translates_known_legacy_permissions() -> None:
    created = RoleCreate.model_validate(
        {"name": "Agent Promoter", "permissions": {"can_promote_agent": True}}
    )

    assert created.capabilities == ["agents.readwrite"]


def test_role_contract_rejects_conflicting_legacy_scope_alias() -> None:
    with pytest.raises(ValidationError, match="Use either capabilities"):
        RoleCreate.model_validate(
            {
                "name": "Conflict",
                "capabilities": ["workflows.read"],
                "scopes": ["agents.read"],
            }
        )


def test_role_contract_preserves_arbitrary_legacy_permissions() -> None:
    created = RoleCreate.model_validate(
        {"name": "Legacy", "permissions": {"read": True}}
    )

    assert created.permissions == {"read": True}
    assert created.capabilities == []


def test_role_public_returns_deprecated_scopes_and_permissions() -> None:
    role = RolePublic.model_validate(
        {
            "id": uuid4(),
            "name": "Compat",
            "created_by": "test@example.com",
            "created_at": "2026-08-20T00:00:00Z",
            "updated_at": "2026-08-20T00:00:00Z",
            "capabilities": ["workflows.read"],
            "permissions": {"read": True},
        }
    )

    assert role.capabilities == ["workflows.read"]
    assert role.scopes == ["workflows.read"]
    assert role.permissions == {"read": True}


def test_bulk_user_replace_roles_accepts_legacy_role_ids() -> None:
    role_id = uuid4()

    operation = BulkUserOperation.model_validate(
        {
            "operation": "replace_roles",
            "user_ids": [str(uuid4())],
            "role_ids": [str(role_id)],
        }
    )

    assert operation.role_ids == [role_id]
    assert operation.role_assignments is None


def test_bulk_user_replace_roles_rejects_conflicting_legacy_role_ids() -> None:
    role_id = uuid4()

    with pytest.raises(ValidationError, match="Use either role_assignments"):
        BulkUserOperation(
            operation="replace_roles",
            user_ids=[uuid4()],
            role_ids=[uuid4()],
            role_assignments=[
                RoleAssignmentSelection(
                    role_id=role_id,
                    boundaries=[
                        RoleAssignmentBoundaryInput(
                            boundary_kind="organization",
                            organization_id=uuid4(),
                        )
                    ],
                )
            ],
        )


def test_assign_users_to_role_accepts_missing_boundaries_for_legacy_callers() -> None:
    request = AssignUsersToRoleRequest.model_validate({"user_ids": [str(uuid4())]})

    assert request.boundaries is None


def test_legacy_role_assignment_boundary_infers_home_org() -> None:
    organization_id = uuid4()
    authorization = _authorization_context(
        boundary=AuthorizationBoundary.organization(organization_id)
    )

    boundaries = infer_legacy_role_assignment_boundaries(authorization)

    assert len(boundaries) == 1
    assert boundaries[0].kind == "organization"
    assert boundaries[0].organization_id == organization_id


def test_legacy_role_assignment_boundary_infers_platform_for_platform_admin() -> None:
    authorization = _authorization_context(
        boundary=AuthorizationBoundary.platform(),
        principal=_principal(scopes=[PLATFORM_SUPERUSER_SCOPE]),
    )

    boundaries = infer_legacy_role_assignment_boundaries(authorization)

    assert len(boundaries) == 1
    assert boundaries[0].kind == "platform"


def test_legacy_role_assignment_boundary_infers_managed_selection() -> None:
    authorization = _authorization_context(
        boundary=AuthorizationBoundary.managed_organizations()
    )

    boundaries = infer_legacy_role_assignment_boundaries(authorization)

    assert len(boundaries) == 1
    assert boundaries[0].kind == "managed_organizations"


def test_packaged_and_server_legacy_role_translation_match() -> None:
    from bifrost.authorization_legacy import (
        LEGACY_CAPABILITY_RENAMES as cli_renames,
        translate_legacy_role_capabilities as cli_translate,
    )
    from shared.authorization_legacy import (
        LEGACY_CAPABILITY_RENAMES as server_renames,
        translate_legacy_role_capabilities as server_translate,
    )

    legacy_scopes = [
        "solutions.build",
        "agents.write",
        "files.content.read",
        "organization.impersonation",
    ]
    legacy_permissions = {"can_promote_agent": True, "custom_metadata": "kept"}

    assert cli_renames == server_renames
    assert cli_translate(legacy_scopes, legacy_permissions) == server_translate(
        legacy_scopes,
        legacy_permissions,
    )


def test_principal_has_exact_scope_and_compatibility_wildcard() -> None:
    builder = _principal(scopes=[BUILDER_EXECUTE_SCOPE])
    assert builder.has_scope(BUILDER_EXECUTE_SCOPE)
    assert not builder.has_scope("roles.readwrite")

    scope_admin = _principal(scopes=[PLATFORM_SUPERUSER_SCOPE])
    assert scope_admin.has_scope("roles.readwrite")

    legacy_admin = _principal(is_superuser=True)
    assert legacy_admin.has_scope("roles.readwrite")


def test_readwrite_implies_read_but_not_execute() -> None:
    expanded = implied_scopes(["agents.readwrite"])

    assert "agents.readwrite" in expanded
    assert "agents.read" in expanded
    assert "agents.execute" not in expanded

    principal = _principal(scopes=["agents.readwrite"])
    assert principal.has_scope("agents.read")
    assert not principal.has_scope("agents.execute")


def test_every_declared_operation_scope_is_grantable() -> None:
    """An operation may only require a scope a role can actually hold.

    The catalog validator checks scope SHAPE (`resource.verb`), which a
    nonexistent key like `agents.write` passes. Before these scopes were
    defined, 81 of 93 catalogued operations declared one that existed nowhere
    in AUTHORIZATION_SCOPE_CATALOG — so enforcing action_scopes would have
    denied every caller except a superuser, and the test suite would not have
    noticed because it runs as platform admin.

    This asserts the two catalogs agree. validate_operation_catalog() enforces
    the same rule at import; this test states it independently so the intent
    survives a refactor of the validator.
    """
    from src.services.operation_catalog import OPERATION_CATALOG

    grantable = {scope.key for scope in AUTHORIZATION_SCOPE_CATALOG}
    declared = {
        scope for operation in OPERATION_CATALOG for scope in operation.action_scopes
    }

    missing = sorted(declared - grantable)
    assert not missing, (
        "operation scope(s) declared but not defined in "
        f"AUTHORIZATION_SCOPE_CATALOG, so no role can grant them: {missing}"
    )


def test_entity_administration_scopes_are_assignable_to_custom_roles() -> None:
    """Entity read/write scopes must be grantable through a custom role.

    They exist so an operator can compose a role like "may manage tables and
    workflows". A scope marked unassignable could only ever be held by a
    superuser, which would make per-user capability resolution meaningless.
    """
    from src.services.operation_catalog import OPERATION_CATALOG

    declared = {
        scope for operation in OPERATION_CATALOG for scope in operation.action_scopes
    }
    by_key = {scope.key: scope for scope in AUTHORIZATION_SCOPE_CATALOG}

    unassignable = sorted(
        key
        for key in declared
        if key in by_key and not by_key[key].assignable_to_custom_roles
    )
    assert not unassignable, (
        f"operation scope(s) cannot be assigned to a custom role: {unassignable}"
    )


def test_platform_admin_write_scopes_are_marked_privileged() -> None:
    """Platform-admin-gated write scopes should render as privileged."""
    by_key = {scope.key: scope for scope in AUTHORIZATION_SCOPE_CATALOG}

    assert by_key["organizations.readwrite"].is_privileged
    assert by_key["roles.readwrite"].is_privileged


def test_catalog_contains_no_reach_or_rejected_action_dialects() -> None:
    keys = {scope.key for scope in AUTHORIZATION_SCOPE_CATALOG}

    assert not any(key.endswith(".all") for key in keys)
    assert not any(key.endswith(".write") for key in keys)
    assert not any(key.endswith(".build") for key in keys)
    assert not any(key.endswith(".publish") for key in keys)
    assert "solutions.build.execute" in keys
    assert "solutions.publish.execute" in keys
    assert "apps.deploy.execute" in keys


def test_default_role_capabilities_are_cataloged_and_keep_operator_out_of_builder() -> (
    None
):
    catalog = {scope.key for scope in AUTHORIZATION_SCOPE_CATALOG}

    assert not {
        capability
        for role in DEFAULT_ROLES_V1
        for capability in role.capabilities
        if capability not in catalog
    }
    assert "builder.execute" in BUILDER_CAPABILITIES
    assert "builder.execute" in PLATFORM_BUILDER_CAPABILITIES
    assert "repository.readwrite" in PLATFORM_BUILDER_CAPABILITIES
    assert {
        "organizations.read",
        "roles.read",
        "claims.read",
        "configs.read",
        "events.read",
        "filepolicies.read",
        "integrations.read",
        "policyrules.read",
    }.isdisjoint(BUILDER_CAPABILITIES)
    assert "builder.execute" not in PLATFORM_OPERATOR_CAPABILITIES
    assert "repository.readwrite" not in PLATFORM_OPERATOR_CAPABILITIES
    assert set(PLATFORM_OPERATOR_CAPABILITIES).isdisjoint(
        DIRECT_WORKSPACE_CAPABILITIES
    )


def test_organization_member_default_does_not_expose_admin_surfaces() -> None:
    """Migrated ordinary org users keep resource access, not admin UI access."""

    assert {"agents.read", "apps.read", "forms.read", "workflows.read"}.issubset(
        ORGANIZATION_MEMBER_CAPABILITIES
    )
    assert {"agents.execute", "workflows.execute"}.issubset(
        ORGANIZATION_MEMBER_CAPABILITIES
    )
    assert {
        "organizations.read",
        "roles.read",
        "claims.read",
        "configs.read",
        "events.read",
        "filepolicies.read",
        "integrations.read",
        "policyrules.read",
    }.isdisjoint(ORGANIZATION_MEMBER_CAPABILITIES)
