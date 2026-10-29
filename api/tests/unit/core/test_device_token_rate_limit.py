"""Device-token polling has a separate, protocol-compatible rate budget."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request


@pytest.mark.asyncio
async def test_device_token_limiter_allows_twenty_polls_in_testing():
    """Exercise the handler with limiting enabled because tests normally bypass it."""
    from src.models.contracts.auth import DeviceTokenRequest
    from src.routers import auth

    assert auth.auth_limiter.max_requests == 10
    assert auth.device_token_limiter.max_requests == 20
    assert auth.device_token_limiter.window_seconds == 60

    pipeline = AsyncMock()
    pipeline.incr = MagicMock()
    pipeline.expire = MagicMock()
    pipeline.execute = AsyncMock(return_value=[1, True])

    redis = AsyncMock()
    redis.incr = AsyncMock(side_effect=range(1, 22))
    redis.expire = AsyncMock()
    redis.ttl = AsyncMock(return_value=40)
    redis.pipeline = MagicMock(return_value=pipeline)
    redis.get = AsyncMock(
        return_value=json.dumps({"status": "pending", "user_code": "ABCD-EFGH"})
    )

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/device/token",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )

    with (
        patch("src.core.rate_limit.get_settings", return_value=SimpleNamespace(is_testing=False)),
        patch("src.core.rate_limit.get_shared_redis", return_value=redis),
        patch("src.routers.auth.get_shared_redis", return_value=redis),
    ):
        for _ in range(20):
            response = await auth.exchange_device_token(
                request,
                DeviceTokenRequest(device_code="device-code"),
            )
            assert response.error == "authorization_pending"

        with pytest.raises(HTTPException) as exc_info:
            await auth.exchange_device_token(
                request,
                DeviceTokenRequest(device_code="device-code"),
            )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "40"}
