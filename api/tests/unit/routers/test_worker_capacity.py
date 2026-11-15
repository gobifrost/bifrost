"""Worker-capacity routes use their Global evaluator permissions before work."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from shared.models import (
    CloudflareCapacityConnectAccepted,
    CloudflareCapacityConnectRequest,
    CloudflareCapacityStatus,
)
from src.core.principal import UserPrincipal
from src.routers import worker_capacity as router
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import entry_for_operation
from src.services.authorization.evaluator import GLOBAL, decide


def _user() -> UserPrincipal:
    return UserPrincipal(user_id=uuid4(), email="operator@example.test", organization_id=None)


def test_worker_capacity_access_list_requires_global_platform_permissions() -> None:
    user = _user()
    context = AuthorizationContext(
        user_id=user.user_id, home_organization_id=None, base_role_id=uuid4(),
        base_permissions=frozenset(), is_external=False,
    )
    get_entry = entry_for_operation("GET /api/admin/worker-capacity/cloudflare")
    post_entry = entry_for_operation("POST /api/admin/worker-capacity/cloudflare")

    assert (get_entry.permission, post_entry.permission) == ("platform.read", "platform.readwrite")
    assert not decide(context, get_entry, GLOBAL).allowed
    assert not decide(context, post_entry, GLOBAL).allowed


@pytest.mark.asyncio
async def test_get_authorizes_global_read_and_exposes_only_status_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize = AsyncMock()
    describe = AsyncMock(return_value=CloudflareCapacityStatus(
        configured=True, active=True, status="ready", scope="bifrost:capacity",
    ))
    user = _user()
    db = object()
    monkeypatch.setattr(router, "authorize_operation", authorize)
    monkeypatch.setattr(router, "describe_cloudflare_capacity", describe)

    response = await router.get_cloudflare_capacity(db, user)

    authorize.assert_awaited_once_with(
        db, user, "GET /api/admin/worker-capacity/cloudflare", GLOBAL,
    )
    assert "api_key" not in response.model_dump()
    assert "capacity_token" not in response.model_dump()


@pytest.mark.asyncio
async def test_connect_authorizes_before_staging_and_returns_only_redacted_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize = AsyncMock()
    setup = AsyncMock()
    publish = AsyncMock()
    user = _user()
    job_id = uuid4()
    accepted = CloudflareCapacityConnectAccepted(
        job_id=job_id,
        connection=CloudflareCapacityStatus(configured=True, status="provisioning", last_job_id=job_id),
    )
    setup.return_value = (accepted, SimpleNamespace(id=job_id))
    monkeypatch.setattr(router, "authorize_operation", authorize)
    monkeypatch.setattr(router, "enqueue_cloudflare_setup", setup)
    monkeypatch.setattr(router, "publish_platform_job_update", publish)
    db = SimpleNamespace(commit=AsyncMock())
    request = CloudflareCapacityConnectRequest(api_key=SecretStr("cloudflare-api-key-that-must-not-leak"))

    response = await router.connect_cloudflare_capacity(request, db, user)

    authorize.assert_awaited_once_with(
        db, user, "POST /api/admin/worker-capacity/cloudflare", GLOBAL,
    )
    db.commit.assert_awaited_once()
    publish.assert_awaited_once_with(setup.return_value[1])
    public = response.model_dump_json()
    assert "cloudflare-api-key-that-must-not-leak" not in public
    assert "capacity_token" not in public


@pytest.mark.asyncio
async def test_connect_stops_before_any_setup_when_global_permission_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))
    setup = AsyncMock()
    monkeypatch.setattr(router, "authorize_operation", denied)
    monkeypatch.setattr(router, "enqueue_cloudflare_setup", setup)
    user = _user()

    with pytest.raises(HTTPException, match="Forbidden") as raised:
        await router.connect_cloudflare_capacity(
            CloudflareCapacityConnectRequest(api_key=SecretStr("cloudflare-api-key-that-must-not-leak")),
            SimpleNamespace(commit=AsyncMock()), user,
        )
    assert raised.value.status_code == 403
    setup.assert_not_awaited()
