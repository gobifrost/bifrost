"""Capability and exact-boundary checks for embed secret administration."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers.app_embed_secrets import _authorized_app
from src.routers.form_embed_secrets import _authorized_form
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _context(*, capability: str, organization_id=None) -> AuthorizationContext:
    home = organization_id or uuid4()
    principal = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=home,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.organization(home),
        effective_capabilities=frozenset({capability}),
        grant_sources=(),
    )


def _router_context(resource) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = resource
    context = MagicMock()
    context.db.execute = AsyncMock(return_value=result)
    return context


@pytest.mark.asyncio
async def test_app_embed_secret_rejects_cross_boundary_app() -> None:
    organization_id = uuid4()
    authorization = _context(
        capability="apps.readwrite",
        organization_id=organization_id,
    )
    app = MagicMock(organization_id=uuid4())

    with pytest.raises(HTTPException) as exc:
        await _authorized_app(_router_context(app), authorization, uuid4())

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_form_embed_secret_requires_forms_readwrite() -> None:
    organization_id = uuid4()
    authorization = _context(
        capability="forms.read",
        organization_id=organization_id,
    )
    form = MagicMock(organization_id=organization_id)

    with pytest.raises(HTTPException) as exc:
        await _authorized_form(_router_context(form), authorization, uuid4())

    assert exc.value.status_code == 403
