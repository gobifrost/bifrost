"""Device decisions handle expired and completed requests without a new TTL."""

from unittest.mock import AsyncMock, patch

import pytest

from shared.device_auth import decide_device_login
from shared.models import DeviceAuthorizeRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("result,expected", [(0, "expired"), (1, "ok"), (2, "decided")])
@pytest.mark.parametrize("approved", [True, False])
async def test_records_explicit_decision(result, expected, approved):
    redis = AsyncMock()
    redis.get.return_value = b"device-code"
    redis.eval.return_value = result
    with patch("shared.device_auth.get_shared_redis", return_value=redis):
        assert await decide_device_login("ABCD-EFGH", "user-id", approved) == expected
    assert redis.eval.call_args.args[1:] == (
        1, "bifrost:auth:device:device-code", "ABCD-EFGH",
        "authorized" if approved else "denied", "user-id",
    )
    redis.setex.assert_not_called()


@pytest.mark.asyncio
async def test_missing_code_does_not_record_a_decision():
    redis = AsyncMock()
    redis.get.return_value = None
    with patch("shared.device_auth.get_shared_redis", return_value=redis):
        assert await decide_device_login("ABCD-EFGH", "user-id", False) == "expired"
    redis.eval.assert_not_called()


def test_public_model_exports_resolve_the_shared_request():
    from src.models import DeviceAuthorizeRequest as root_request
    from src.models.contracts import DeviceAuthorizeRequest as contract_request

    assert root_request is DeviceAuthorizeRequest
    assert contract_request is DeviceAuthorizeRequest
