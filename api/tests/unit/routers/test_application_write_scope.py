"""Write-scope enforcement for application mutations.

``get_application_for_write_or_404`` is the single write-scope gate reused
by every mutating route in ``applications.py``. These tests exercise the
gate directly: read access is unchanged (delegated to
``get_application_by_id_or_404``), but writing additionally requires scope
bypass (platform admin or provider-org member) or that the application
belongs to the caller's own organization. A global application
(``organization_id is None``) can only be written by a bypass caller.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.applications import get_application_for_write_or_404


def _ctx(*, org_id, is_platform_admin=False, is_provider_org=False):
    return SimpleNamespace(
        org_id=org_id,
        user=SimpleNamespace(
            is_platform_admin=is_platform_admin,
            is_provider_org=is_provider_org,
        ),
    )


@pytest.mark.asyncio
async def test_non_bypass_member_denied_for_global_app():
    app_id = uuid4()
    application = SimpleNamespace(id=app_id, organization_id=None)
    ctx = _ctx(org_id=uuid4())

    with patch(
        "src.routers.applications.get_application_by_id_or_404",
        new=AsyncMock(return_value=application),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_application_for_write_or_404(ctx, app_id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == f"Application '{app_id}' not found"


@pytest.mark.asyncio
async def test_non_bypass_member_allowed_for_own_org_app():
    app_id = uuid4()
    org_id = uuid4()
    application = SimpleNamespace(id=app_id, organization_id=org_id)
    ctx = _ctx(org_id=org_id)

    with patch(
        "src.routers.applications.get_application_by_id_or_404",
        new=AsyncMock(return_value=application),
    ):
        result = await get_application_for_write_or_404(ctx, app_id)

    assert result is application


@pytest.mark.asyncio
async def test_non_bypass_member_denied_for_other_org_app():
    app_id = uuid4()
    application = SimpleNamespace(id=app_id, organization_id=uuid4())
    ctx = _ctx(org_id=uuid4())

    with patch(
        "src.routers.applications.get_application_by_id_or_404",
        new=AsyncMock(return_value=application),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_application_for_write_or_404(ctx, app_id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_platform_admin_allowed_for_global_app():
    app_id = uuid4()
    application = SimpleNamespace(id=app_id, organization_id=None)
    ctx = _ctx(org_id=uuid4(), is_platform_admin=True)

    with patch(
        "src.routers.applications.get_application_by_id_or_404",
        new=AsyncMock(return_value=application),
    ):
        result = await get_application_for_write_or_404(ctx, app_id)

    assert result is application


@pytest.mark.asyncio
async def test_provider_org_non_admin_allowed_for_global_app():
    """Bypass is is_platform_admin OR is_provider_org — either flag alone
    must be sufficient to write a global application."""
    app_id = uuid4()
    application = SimpleNamespace(id=app_id, organization_id=None)
    ctx = _ctx(org_id=uuid4(), is_platform_admin=False, is_provider_org=True)

    with patch(
        "src.routers.applications.get_application_by_id_or_404",
        new=AsyncMock(return_value=application),
    ):
        result = await get_application_for_write_or_404(ctx, app_id)

    assert result is application
