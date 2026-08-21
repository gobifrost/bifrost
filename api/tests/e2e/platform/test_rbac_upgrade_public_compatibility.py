"""Upgrade and public-compatibility proof for boundary-aware RBAC."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bifrost.commands.roles import roles_group
from bifrost.contracts.users import RoleCreate as SdkRoleCreate
from bifrost.contracts.users import RoleUpdate as SdkRoleUpdate
from src.core.constants import (
    BUILDER_ROLE_ID,
    ORGANIZATION_MEMBER_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_BUILDER_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
    PROVIDER_ORG_ID,
)
from src.core.principal import UserPrincipal
from src.core.security import decode_token
from src.models.orm.role_assignments import RoleAssignment
from src.models.orm.users import User
from src.services.authorization import (
    AuthorizationBoundary,
    resolve_authorization_context,
)


def _assignment_by_role(assignments: list[RoleAssignment], role_id: UUID) -> RoleAssignment:
    matches = [assignment for assignment in assignments if assignment.role_id == role_id]
    assert len(matches) == 1
    return matches[0]


async def _assignments_for(db_session, user_id: UUID) -> list[RoleAssignment]:
    result = await db_session.execute(
        select(RoleAssignment)
        .options(selectinload(RoleAssignment.boundaries))
        .where(RoleAssignment.user_id == user_id)
        .order_by(RoleAssignment.role_id)
    )
    return list(result.scalars().all())


def _invoke_roles(invoke_cli):
    return lambda args: invoke_cli(roles_group, args)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_upgrade_default_roles_and_public_user_claims(
    e2e_client,
    platform_admin,
    org1_user,
    db_session,
) -> None:
    """Post-upgrade users keep old public claims while gaining scoped Roles."""

    admin_assignments = await _assignments_for(db_session, platform_admin.user_id)
    admin_assignment = _assignment_by_role(admin_assignments, PLATFORM_ADMIN_ROLE_ID)
    assert [(b.boundary_kind, b.organization_id) for b in admin_assignment.boundaries] == [
        ("platform", None)
    ]

    admin_me = e2e_client.get("/auth/me", headers=platform_admin.headers)
    assert admin_me.status_code == 200, admin_me.text
    assert admin_me.json()["is_superuser"] is True

    token_claims = decode_token(platform_admin.access_token)
    assert token_claims is not None
    assert token_claims["is_superuser"] is True
    assert token_claims["is_provider_org"] is True

    customer_assignments = await _assignments_for(db_session, org1_user.user_id)
    customer_member = _assignment_by_role(
        customer_assignments,
        ORGANIZATION_MEMBER_ROLE_ID,
    )
    assert [
        (boundary.boundary_kind, boundary.organization_id)
        for boundary in customer_member.boundaries
    ] == [("organization", org1_user.organization_id)]
    assert all(
        assignment.role_id != PLATFORM_OPERATOR_ROLE_ID
        for assignment in customer_assignments
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_provider_non_admin_gets_operator_without_builder_execute(
    e2e_client,
    platform_admin,
    db_session,
) -> None:
    """Provider staff retain support authority without gaining Builder rights."""

    email = f"provider-operator-{uuid4().hex[:8]}@gobifrost.com"
    created = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": email,
            "name": "Provider Operator",
            "organization_id": str(PROVIDER_ORG_ID),
            "is_superuser": False,
        },
    )
    assert created.status_code == 201, created.text
    user_id = UUID(created.json()["id"])

    assignments = await _assignments_for(db_session, user_id)
    member = _assignment_by_role(assignments, ORGANIZATION_MEMBER_ROLE_ID)
    operator = _assignment_by_role(assignments, PLATFORM_OPERATOR_ROLE_ID)
    assert [(b.boundary_kind, b.organization_id) for b in member.boundaries] == [
        ("organization", PROVIDER_ORG_ID)
    ]
    assert [(b.boundary_kind, b.organization_id) for b in operator.boundaries] == [
        ("managed_organizations", None)
    ]
    assert all(assignment.role_id != BUILDER_ROLE_ID for assignment in assignments)

    principal = UserPrincipal(
        user_id=user_id,
        email=email,
        organization_id=PROVIDER_ORG_ID,
        is_superuser=False,
    )
    authorization = await resolve_authorization_context(
        db_session,
        requester=principal,
        selected_boundary=AuthorizationBoundary.managed_organizations(),
    )
    assert authorization.has_capability("organizations.readwrite")
    assert not authorization.has_capability("builder.execute")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_platform_builder_requires_explicit_selected_boundary_without_union(
    e2e_client,
    platform_admin,
    org1_user,
    org1,
    org2,
    db_session,
) -> None:
    """Platform Builder works in assigned Platform/Org boundaries only.

    This pins the acceptance rule that Platform Builder does not imply a
    Managed-organizations union and that selecting Platform is Global, not a
    customer-org mutation wildcard.
    """

    assign = e2e_client.post(
        f"/api/roles/{PLATFORM_BUILDER_ROLE_ID}/users",
        headers=platform_admin.headers,
        json={
            "user_ids": [str(org1_user.user_id)],
            "boundaries": [
                {
                    "boundary_kind": "organization",
                    "organization_id": org1["id"],
                },
                {"boundary_kind": "platform"},
            ],
        },
    )
    assert assign.status_code == 204, assign.text

    org_key: str | None = None
    global_key: str | None = None
    try:
        principal = UserPrincipal(
            user_id=org1_user.user_id,
            email=org1_user.email,
            organization_id=org1_user.organization_id,
            is_superuser=False,
        )

        org_auth = await resolve_authorization_context(
            db_session,
            requester=principal,
            selected_boundary=AuthorizationBoundary.organization(UUID(org1["id"])),
        )
        assert org_auth.has_capability("builder.execute")
        assert org_auth.has_capability("configs.readwrite")
        org_auth.require_resource_boundary(UUID(org1["id"]))
        with pytest.raises(HTTPException):
            org_auth.require_resource_boundary(UUID(org2["id"]))

        platform_auth = await resolve_authorization_context(
            db_session,
            requester=principal,
            selected_boundary=AuthorizationBoundary.platform(),
        )
        assert platform_auth.has_capability("builder.execute")
        assert platform_auth.has_capability("repository.readwrite")
        platform_auth.require_resource_boundary(None)
        with pytest.raises(HTTPException):
            platform_auth.require_resource_boundary(UUID(org1["id"]))

        managed_auth = await resolve_authorization_context(
            db_session,
            requester=principal,
            selected_boundary=AuthorizationBoundary.managed_organizations(),
        )
        assert not managed_auth.has_capability("builder.execute")
        assert not managed_auth.has_capability("configs.readwrite")

        org_headers = {
            **org1_user.headers,
            "X-Bifrost-Boundary": f"organization:{org1['id']}",
        }
        org_key = f"platform_builder_org_{uuid4().hex[:8]}"
        org_create = e2e_client.post(
            "/api/config",
            headers=org_headers,
            json={
                "key": org_key,
                "value": "enabled",
                "type": "string",
                "organization_id": org1["id"],
            },
        )
        assert org_create.status_code == 201, org_create.text

        denied_cross_org = e2e_client.post(
            "/api/config",
            headers=org_headers,
            json={
                "key": f"platform_builder_cross_{uuid4().hex[:8]}",
                "value": "denied",
                "type": "string",
                "organization_id": org2["id"],
            },
        )
        assert denied_cross_org.status_code == 409, denied_cross_org.text

        denied_managed = e2e_client.post(
            "/api/config",
            headers={
                **org1_user.headers,
                "X-Bifrost-Boundary": "managed_organizations",
            },
            json={
                "key": f"platform_builder_managed_{uuid4().hex[:8]}",
                "value": "denied",
                "type": "string",
                "organization_id": org1["id"],
            },
        )
        assert denied_managed.status_code == 403, denied_managed.text

        platform_headers = {
            **org1_user.headers,
            "X-Bifrost-Boundary": "platform",
        }
        denied_platform_customer = e2e_client.post(
            "/api/config",
            headers=platform_headers,
            json={
                "key": f"platform_builder_platform_org_{uuid4().hex[:8]}",
                "value": "denied",
                "type": "string",
                "organization_id": org1["id"],
            },
        )
        assert denied_platform_customer.status_code == 409, denied_platform_customer.text

        global_key = f"platform_builder_global_{uuid4().hex[:8]}"
        global_create = e2e_client.post(
            "/api/config",
            headers=platform_headers,
            json={
                "key": global_key,
                "value": "enabled",
                "type": "string",
                "organization_id": None,
            },
        )
        assert global_create.status_code == 201, global_create.text
    finally:
        if org_key is not None:
            configs = e2e_client.get(
                "/api/config",
                headers={
                    **org1_user.headers,
                    "X-Bifrost-Boundary": f"organization:{org1['id']}",
                },
            )
            if configs.status_code == 200:
                for row in configs.json():
                    if row["key"] == org_key:
                        e2e_client.delete(
                            f"/api/config/{row['id']}",
                            headers={
                                **org1_user.headers,
                                "X-Bifrost-Boundary": f"organization:{org1['id']}",
                            },
                        )
        if global_key is not None:
            configs = e2e_client.get(
                "/api/config",
                headers={
                    **org1_user.headers,
                    "X-Bifrost-Boundary": "platform",
                },
            )
            if configs.status_code == 200:
                for row in configs.json():
                    if row["key"] == global_key:
                        e2e_client.delete(
                            f"/api/config/{row['id']}",
                            headers={
                                **org1_user.headers,
                                "X-Bifrost-Boundary": "platform",
                            },
                        )
        e2e_client.delete(
            f"/api/roles/{PLATFORM_BUILDER_ROLE_ID}/users/{org1_user.user_id}",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
def test_role_public_compatibility_and_cli_sdk_legacy_dto_parsing(
    e2e_client,
    platform_admin,
    org1_user,
    cli_client,
    invoke_cli,
) -> None:
    """Role payloads retain permissions/scopes while capabilities are canonical."""

    sdk_create = SdkRoleCreate(
        name="SDK Legacy",
        scopes=["agents.write"],
        permissions={"can_promote_agent": True, "custom_flag": "kept"},
    )
    assert sdk_create.capabilities == ["agents.readwrite"]
    assert sdk_create.permissions == {"can_promote_agent": True, "custom_flag": "kept"}
    sdk_update = SdkRoleUpdate(scopes=["solutions.build"])
    assert "solutions.build.execute" in (sdk_update.capabilities or [])

    name = f"legacy-role-{uuid4().hex[:8]}"
    response = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={
            "name": name,
            "scopes": ["agents.write"],
            "permissions": {"can_promote_agent": True, "custom_flag": "kept"},
        },
    )
    assert response.status_code == 201, response.text
    role = response.json()
    role_id = role["id"]
    assert role["capabilities"] == ["agents.readwrite"]
    assert role["scopes"] == ["agents.readwrite"]
    assert role["permissions"] == {
        "can_promote_agent": True,
        "custom_flag": "kept",
    }

    try:
        assign = e2e_client.post(
            f"/api/roles/{role_id}/users",
            headers=platform_admin.headers,
            json={
                "user_ids": [str(org1_user.user_id)],
                "boundaries": [
                    {
                        "boundary_kind": "organization",
                        "organization_id": str(org1_user.organization_id),
                    }
                ],
            },
        )
        assert assign.status_code == 204, assign.text

        legacy_shape = e2e_client.get(
            f"/api/users/{org1_user.user_id}/roles",
            headers=platform_admin.headers,
        )
        assert legacy_shape.status_code == 200, legacy_shape.text
        assert set(legacy_shape.json()) == {"role_ids"}
        assert role_id in legacy_shape.json()["role_ids"]

        detail_shape = e2e_client.get(
            f"/api/users/{org1_user.user_id}/role-assignments",
            headers=platform_admin.headers,
        )
        assert detail_shape.status_code == 200, detail_shape.text
        assert any(item["role_id"] == role_id for item in detail_shape.json())

        cli_result = _invoke_roles(invoke_cli)(["--json", "get", role_id])
        assert cli_result.exit_code == 0, cli_result.output
        cli_payload = json.loads(cli_result.output)
        assert cli_payload["permissions"]["custom_flag"] == "kept"
        assert cli_payload["scopes"] == ["agents.readwrite"]
    finally:
        e2e_client.delete(
            f"/api/roles/{role_id}/users/{org1_user.user_id}",
            headers=platform_admin.headers,
        )
        e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_platform_admin_assignment_and_legacy_bit_stay_synchronized(
    e2e_client,
    platform_admin,
    org1,
    db_session,
) -> None:
    email = f"admin-sync-{uuid4().hex[:8]}@gobifrost.dev"
    created = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": email,
            "name": "Admin Sync",
            "organization_id": org1["id"],
            "is_superuser": False,
        },
    )
    assert created.status_code == 201, created.text
    user_id = UUID(created.json()["id"])

    roles = e2e_client.get("/api/roles", headers=platform_admin.headers)
    assert roles.status_code == 200, roles.text
    platform_admin_role_id = next(
        role["id"] for role in roles.json() if role["key"] == "platform_admin"
    )

    assign = e2e_client.post(
        f"/api/roles/{platform_admin_role_id}/users",
        headers=platform_admin.headers,
        json={
            "user_ids": [str(user_id)],
            "boundaries": [{"boundary_kind": "platform"}],
        },
    )
    assert assign.status_code == 204, assign.text

    db_session.expire_all()
    promoted = await db_session.get(User, user_id)
    assert promoted is not None
    assert promoted.is_superuser is True
    promoted_assignment = _assignment_by_role(
        await _assignments_for(db_session, user_id),
        PLATFORM_ADMIN_ROLE_ID,
    )
    assert [(b.boundary_kind, b.organization_id) for b in promoted_assignment.boundaries] == [
        ("platform", None)
    ]

    remove = e2e_client.delete(
        f"/api/roles/{platform_admin_role_id}/users/{user_id}",
        headers=platform_admin.headers,
    )
    assert remove.status_code == 204, remove.text

    db_session.expire_all()
    demoted = await db_session.get(User, user_id)
    assert demoted is not None
    assert demoted.is_superuser is False
    assert all(
        assignment.role_id != PLATFORM_ADMIN_ROLE_ID
        for assignment in await _assignments_for(db_session, user_id)
    )
