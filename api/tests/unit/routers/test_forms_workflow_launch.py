"""Authorization boundaries for form-triggered workflow launches."""

from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.routers import forms


def _caller(*, home, permissions=()) -> Caller:
    principal = UserPrincipal(
        user_id=uuid4(), email="forms-launch@contoso.example", organization_id=home
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=home,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(permissions),
            role_grants=(),
        ),
    )


def _context(*, embed=False, org_id=None):
    return SimpleNamespace(
        user=SimpleNamespace(embed=embed),
        org_id=org_id,
    )


@pytest.mark.asyncio
async def test_authenticated_global_form_requires_execute_at_original_home(monkeypatch):
    home = uuid4()
    caller = _caller(home=home)
    monkeypatch.setattr(forms, "load_caller", AsyncMock(return_value=caller))

    with pytest.raises(HTTPException) as exc_info:
        await forms._form_launch_authority(
            AsyncMock(), _context(org_id=uuid4()), SimpleNamespace(organization_id=None)
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_authenticated_global_form_uses_original_home_execute_grant(monkeypatch):
    home = uuid4()
    caller = _caller(home=home, permissions={"workflows.execute"})
    monkeypatch.setattr(forms, "load_caller", AsyncMock(return_value=caller))

    execution_org, resolved_caller = await forms._form_launch_authority(
        AsyncMock(), _context(org_id=uuid4()), SimpleNamespace(organization_id=None)
    )

    assert execution_org == home
    assert resolved_caller is caller


@pytest.mark.asyncio
async def test_authenticated_form_cannot_use_home_execute_grant_for_foreign_org(monkeypatch):
    home = uuid4()
    caller = _caller(home=home, permissions={"workflows.execute"})
    monkeypatch.setattr(forms, "load_caller", AsyncMock(return_value=caller))

    with pytest.raises(HTTPException) as exc_info:
        await forms._form_launch_authority(
            AsyncMock(), _context(org_id=home), SimpleNamespace(organization_id=uuid4())
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_bound_embed_form_keeps_its_resource_capability_without_human_grant(monkeypatch):
    load_caller = AsyncMock()
    monkeypatch.setattr(forms, "load_caller", load_caller)
    form_org = uuid4()

    execution_org, caller = await forms._form_launch_authority(
        AsyncMock(), _context(embed=True, org_id=form_org), SimpleNamespace(organization_id=None)
    )

    assert execution_org == form_org
    assert caller is None
    load_caller.assert_not_awaited()
