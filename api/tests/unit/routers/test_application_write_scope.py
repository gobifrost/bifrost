"""The application write helper keeps the persisted App as its permission target."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.applications import get_application_for_write_or_404


@pytest.mark.asyncio
@pytest.mark.parametrize("organization_id", [None, uuid4()])
async def test_write_lookup_requests_named_write_permission(organization_id):
    """The lower lookup evaluates ``apps.readwrite`` at the App's true scope.

    The caller's home org must not replace this target: a Global App and an
    organization App have different named-permission boundaries.
    """
    app_id = uuid4()
    application = SimpleNamespace(id=app_id, organization_id=organization_id)
    ctx = SimpleNamespace()

    with patch(
        "src.routers.applications.get_application_by_id_or_404",
        new=AsyncMock(return_value=application),
    ) as lookup:
        result = await get_application_for_write_or_404(ctx, app_id)

    assert result is application
    lookup.assert_awaited_once_with(ctx, app_id, permission="apps.readwrite")


@pytest.mark.asyncio
async def test_write_lookup_preserves_neutral_not_found_denial():
    """A lower authorization refusal remains the non-enumerating 404."""
    app_id = uuid4()
    refusal = HTTPException(status_code=404, detail=f"Application '{app_id}' not found")

    with patch(
        "src.routers.applications.get_application_by_id_or_404",
        new=AsyncMock(side_effect=refusal),
    ):
        with pytest.raises(HTTPException) as raised:
            await get_application_for_write_or_404(SimpleNamespace(), app_id)

    assert raised.value is refusal
