"""Database-backed admission tests for role resource bindings."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.forms import Form, FormRole
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.services.authorization.enforce import load_caller


async def _organization(db_session, label: str) -> Organization:
    organization = Organization(
        name=f"roles-{label}-{uuid4().hex[:8]}",
        created_by="roles-authorization-test",
    )
    db_session.add(organization)
    await db_session.flush()
    return organization


async def _caller_with_role(
    db_session,
    *,
    home: Organization,
    permissions: tuple[str, ...],
    boundary_kind: str,
    boundary_organization_id=None,
):
    user = User(
        email=f"roles-{uuid4().hex}@example.test",
        name="Role Admission User",
        organization_id=home.id,
        base_role_id=USER_ROLE_ID,
    )
    role = Role(name=f"Role Admission {uuid4().hex}", created_by="roles-authorization-test")
    db_session.add_all([user, role])
    await db_session.flush()
    db_session.add_all(
        [RolePermission(role_id=role.id, permission=permission) for permission in permissions]
        + [
            UserRole(user_id=user.id, role_id=role.id, assigned_by="roles-authorization-test"),
            UserRoleBoundary(
                user_id=user.id,
                role_id=role.id,
                kind=boundary_kind,
                organization_id=boundary_organization_id,
            ),
        ]
    )
    await db_session.flush()
    return await load_caller(
        db_session,
        UserPrincipal(
            user_id=user.id,
            email=user.email,
            organization_id=user.organization_id,
            is_active=True,
        ),
    )


@pytest.mark.asyncio
async def test_role_form_list_and_summary_filter_bindings_before_aggregation(db_session):
    """A Global role-definition reader only sees bindings in original reach."""
    from shared.sdk_roles import get_role
    from src.routers.roles import get_role_forms

    visible_org = await _organization(db_session, "visible")
    hidden_org = await _organization(db_session, "hidden")
    caller = await _caller_with_role(
        db_session,
        home=visible_org,
        permissions=("roles.read",),
        boundary_kind="platform",
    )
    shared_role = Role(name=f"Shared Role {uuid4().hex}", created_by="roles-authorization-test")
    visible_form = Form(
        name="Visible form",
        created_by="roles-authorization-test",
        organization_id=visible_org.id,
    )
    hidden_form = Form(
        name="Hidden form",
        created_by="roles-authorization-test",
        organization_id=hidden_org.id,
    )
    db_session.add_all([shared_role, visible_form, hidden_form])
    await db_session.flush()
    db_session.add_all(
        [
            FormRole(form_id=visible_form.id, role_id=shared_role.id, assigned_by="roles-authorization-test"),
            FormRole(form_id=hidden_form.id, role_id=shared_role.id, assigned_by="roles-authorization-test"),
        ]
    )
    await db_session.flush()

    response = await get_role_forms(shared_role.id, caller.principal, db_session)
    assert response.form_ids == [str(visible_form.id)]

    summary = await get_role(db_session, caller=caller, role_id=shared_role.id)
    assert summary.consumer_counts is not None
    assert summary.consumer_counts.forms == 1


@pytest.mark.asyncio
async def test_role_binding_write_refuses_a_form_outside_current_role_boundary(db_session):
    """A form share is decided at the persisted form organization."""
    from shared.sdk_roles import require_role_binding_target

    allowed_org = await _organization(db_session, "allowed")
    foreign_org = await _organization(db_session, "foreign")
    caller = await _caller_with_role(
        db_session,
        home=allowed_org,
        permissions=("forms.readwrite",),
        boundary_kind="organization",
        boundary_organization_id=allowed_org.id,
    )
    foreign_form = Form(
        name="Foreign form",
        created_by="roles-authorization-test",
        organization_id=foreign_org.id,
    )
    db_session.add(foreign_form)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await require_role_binding_target(
            db_session,
            caller,
            operation="roles.forms.assign",
            resource_model=Form,
            resource_id=foreign_form.id,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_role_binding_private_agent_owned_by_another_user_requires_readwrite_all(db_session):
    """Agent sharing cannot change another user's private Agent with plain RW."""
    from shared.sdk_roles import RoleServiceError, require_role_binding_target

    organization = await _organization(db_session, "private-agent")
    caller = await _caller_with_role(
        db_session,
        home=organization,
        permissions=("agents.readwrite",),
        boundary_kind="organization",
        boundary_organization_id=organization.id,
    )
    other_owner = User(
        email=f"other-owner-{uuid4().hex}@example.test",
        name="Other Owner",
        organization_id=organization.id,
        base_role_id=USER_ROLE_ID,
    )
    db_session.add(other_owner)
    await db_session.flush()
    agent = Agent(
        name="Other private agent",
        description="private sharing target",
        system_prompt="Private",
        channels=["chat"],
        access_level=AgentAccessLevel.PRIVATE,
        organization_id=organization.id,
        owner_user_id=other_owner.id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="roles-authorization-test",
    )
    db_session.add(agent)
    await db_session.flush()

    with pytest.raises(RoleServiceError) as exc_info:
        await require_role_binding_target(
            db_session,
            caller,
            operation="roles.agents.assign",
            resource_model=Agent,
            resource_id=agent.id,
            agent_configuration=True,
        )
    assert exc_info.value.status_code == 403
