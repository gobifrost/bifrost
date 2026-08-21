"""Role route authorization boundary behavior."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.orm.knowledge_sources import KnowledgeNamespaceRole
from src.models.orm.organizations import Organization
from src.models.orm.role_assignments import RoleAssignment, RoleAssignmentBoundary
from src.models.orm.users import Role, User
from src.routers.roles import (
    list_authorization_capabilities,
    list_authorization_scopes,
    list_roles,
)
from src.routers.users import get_user_role_assignments, get_user_roles
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(*, organization_id, capabilities: set[str]) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="role-manager@example.com",
        name="Role Manager",
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def _platform_authorization(*, capabilities: set[str]) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="role-manager@example.com",
        name="Role Manager",
        organization_id=None,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.platform(),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


async def _organization(db: AsyncSession) -> Organization:
    organization = Organization(
        id=uuid4(),
        name=f"role-authz-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=False,
        created_by="test@example.com",
    )
    db.add(organization)
    await db.flush()
    return organization


async def _role(db: AsyncSession) -> Role:
    role = Role(
        id=uuid4(),
        name=f"role-authz-role-{uuid4().hex[:8]}",
        capabilities=[],
        created_by="test@example.com",
    )
    db.add(role)
    await db.flush()
    return role


@pytest.mark.asyncio
async def test_legacy_roles_scopes_alias_matches_capabilities_catalog() -> None:
    authorization = _platform_authorization(capabilities={"roles.read"})

    capabilities = await list_authorization_capabilities(authorization)
    scopes = await list_authorization_scopes(authorization)

    assert scopes == capabilities


async def _user_assignment(
    db: AsyncSession,
    *,
    role: Role,
    organization: Organization,
) -> User:
    user = User(
        id=uuid4(),
        email=f"role-authz-user-{uuid4().hex[:8]}@example.com",
        name="Role Count User",
        organization_id=organization.id,
    )
    assignment = RoleAssignment(
        id=uuid4(),
        user_id=user.id,
        role_id=role.id,
    )
    assignment.boundaries.append(
        RoleAssignmentBoundary(
            boundary_kind="organization",
            organization_id=organization.id,
        )
    )
    db.add_all([user, assignment])
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_list_roles_consumer_counts_are_scoped_to_exact_org(
    db_session: AsyncSession,
) -> None:
    selected_org = await _organization(db_session)
    other_org = await _organization(db_session)
    role = await _role(db_session)
    await _user_assignment(db_session, role=role, organization=selected_org)
    await _user_assignment(db_session, role=role, organization=other_org)
    db_session.add_all(
        [
            KnowledgeNamespaceRole(
                namespace="selected",
                organization_id=selected_org.id,
                role_id=role.id,
            ),
            KnowledgeNamespaceRole(
                namespace="other",
                organization_id=other_org.id,
                role_id=role.id,
            ),
        ]
    )
    await db_session.flush()

    roles = await list_roles(
        _authorization(organization_id=selected_org.id, capabilities={"roles.read"}),
        db_session,
    )

    public = next(item for item in roles if item.id == role.id)
    assert public.consumer_counts is not None
    assert public.consumer_counts.users == 1
    assert public.consumer_counts.knowledge == 1


@pytest.mark.asyncio
async def test_user_roles_endpoint_keeps_legacy_role_ids_shape(
    db_session: AsyncSession,
) -> None:
    organization = await _organization(db_session)
    role = await _role(db_session)
    user = await _user_assignment(db_session, role=role, organization=organization)
    authorization = _authorization(
        organization_id=organization.id,
        capabilities={"roles.read"},
    )

    response = await get_user_roles(str(user.id), authorization, db_session)
    details = await get_user_role_assignments(str(user.id), authorization, db_session)

    assert response.role_ids == [str(role.id)]
    assert len(details) == 1
    assert details[0].role_id == role.id
    assert details[0].boundaries[0].organization_id == organization.id
