"""Authorization contracts for SDK and router form reads."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.enums import FormAccessLevel
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller
from shared.sdk_forms import check_form_access


def _caller(
    *,
    home,
    permissions=(),
    boundary=None,
    role_id=None,
    original_external=False,
    actor_external=False,
) -> Caller:
    role_grants = ()
    if boundary is not None:
        role_grants = (
            RoleGrant(
                role_id or uuid4(),
                frozenset(permissions),
                (Boundary(BoundaryKind.ORGANIZATION, boundary),),
            ),
        )
    principal = UserPrincipal(
        user_id=uuid4(),
        email="forms@contoso.example",
        organization_id=home,
        is_external=actor_external,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=home,
            base_role_id=USER_ROLE_ID,
            is_external=original_external,
            base_permissions=frozenset(),
            role_grants=role_grants,
        ),
    )


def _form(*, organization_id, access_level=FormAccessLevel.ROLE_BASED):
    return SimpleNamespace(
        id=uuid4(), organization_id=organization_id, access_level=access_level
    )


@pytest.mark.asyncio
async def test_role_only_grant_reads_a_role_shared_form_at_its_placement():
    home, target, role_id = uuid4(), uuid4(), uuid4()
    caller = _caller(
        home=home,
        permissions={"forms.readbasic"},
        boundary=target,
        role_id=role_id,
    )
    form = _form(organization_id=target)
    session = AsyncMock()
    session.execute.side_effect = (
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [role_id])),
        SimpleNamespace(scalar_one_or_none=lambda: form.id),
    )

    assert await check_form_access(session, form, caller)


@pytest.mark.asyncio
async def test_readbasic_does_not_bypass_an_unshared_role_form_acl():
    org = uuid4()
    caller = _caller(
        home=org,
        permissions={"forms.readbasic"},
        boundary=org,
    )
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [])
    )

    assert not await check_form_access(session, _form(organization_id=org), caller)


@pytest.mark.asyncio
async def test_forms_read_exposes_management_detail_without_form_acl_membership():
    org = uuid4()
    caller = _caller(home=org, permissions={"forms.read"}, boundary=org)

    assert await check_form_access(AsyncMock(), _form(organization_id=org), caller)


@pytest.mark.asyncio
async def test_shared_global_form_uses_home_permission_unless_global_is_explicit():
    home = uuid4()
    caller = _caller(
        home=home,
        permissions={"forms.readbasic"},
        boundary=home,
    )
    form = _form(organization_id=None, access_level=FormAccessLevel.AUTHENTICATED)

    assert await check_form_access(AsyncMock(), form, caller)
    assert not await check_form_access(
        AsyncMock(), form, caller, selected_organization_id=None
    )


@pytest.mark.asyncio
async def test_role_permission_never_widens_original_user_reach():
    home, granted, foreign = uuid4(), uuid4(), uuid4()
    caller = _caller(
        home=home,
        permissions={"forms.read"},
        boundary=granted,
    )

    assert not await check_form_access(
        AsyncMock(), _form(organization_id=foreign), caller
    )


@pytest.mark.asyncio
async def test_internal_actor_cannot_make_original_external_user_authenticated():
    org = uuid4()
    caller = _caller(
        home=org,
        permissions={"forms.readbasic"},
        boundary=org,
        original_external=True,
        actor_external=False,
    )

    assert not await check_form_access(
        AsyncMock(),
        _form(organization_id=org, access_level=FormAccessLevel.AUTHENTICATED),
        caller,
    )


@pytest.mark.asyncio
async def test_external_actor_does_not_strip_original_internal_authenticated_access():
    org = uuid4()
    caller = _caller(
        home=org,
        permissions={"forms.readbasic"},
        boundary=org,
        original_external=False,
        actor_external=True,
    )

    assert await check_form_access(
        AsyncMock(),
        _form(organization_id=org, access_level=FormAccessLevel.AUTHENTICATED),
        caller,
    )
