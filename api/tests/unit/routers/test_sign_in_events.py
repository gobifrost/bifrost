"""Which sign-in routes publish ``user.signed_in``, and with which method."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from starlette.requests import Request

from src.routers import auth


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        email="kim@example.com",
        name="Kim",
        organization_id=uuid4(),
        is_superuser=False,
        is_active=True,
        identity_kind=None,
        last_login=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["password", "passkey", None])
async def test_login_tokens_publish_the_sign_in_method(method):
    user = _user()
    with (
        patch.object(auth, "issue_human_session", AsyncMock(return_value=SimpleNamespace(access_token="a", refresh_token="r"))),
        patch.object(auth, "emit_audit", AsyncMock()),
        patch.object(auth, "emit_user_signed_in", AsyncMock()) as signed_in,
    ):
        await auth._generate_login_tokens(user, AsyncMock(), None, sign_in_method=method)

    if method is None:
        signed_in.assert_not_awaited()
    else:
        signed_in.assert_awaited_once_with(user, method=method)


@pytest.mark.asyncio
async def test_device_code_exchange_is_not_a_sign_in():
    """A signed-in user authorized the CLI; that already fired its own event."""
    from src.models.contracts.auth import DeviceTokenRequest

    user = _user()
    redis = AsyncMock()
    redis.get = AsyncMock(
        return_value=json.dumps({"status": "authorized", "user_id": str(user.id), "user_code": "ABCD-EFGH"})
    )
    repository = MagicMock()
    repository.get_by_id = AsyncMock(return_value=user)
    request = Request(
        {"type": "http", "method": "POST", "path": "/auth/device/token", "headers": [], "client": ("127.0.0.1", 1)}
    )
    login = AsyncMock(return_value=SimpleNamespace(access_token="a", refresh_token="r", token_type="bearer"))
    with (
        patch.object(auth.device_token_limiter, "check", AsyncMock()),
        patch.object(auth, "decode_token", return_value={"exp": 4102444800}),
        patch.object(auth, "get_shared_redis", AsyncMock(return_value=redis)),
        patch.object(auth, "UserRepository", return_value=repository),
        patch.object(auth, "_generate_login_tokens", login),
    ):
        await auth.exchange_device_token(request, DeviceTokenRequest(device_code="device-code"), AsyncMock())

    assert login.await_args.kwargs["sign_in_method"] is None
