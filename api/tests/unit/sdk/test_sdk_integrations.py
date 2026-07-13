"""HTTP error-contract tests for the Bifrost integrations SDK."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bifrost.client import BifrostAPIError, BifrostAuthenticationError
from bifrost.integrations import integrations


def _response(status_code: int, detail: str = "safe failure") -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"detail": detail, "access_token": "must-not-appear"},
        request=httpx.Request("POST", "https://api.example/api/sdk/integrations/get"),
    )


@pytest.mark.asyncio
async def test_get_returns_none_only_for_successful_null_response() -> None:
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        200,
        content=b"null",
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "https://api.example/api/sdk/integrations/get"),
    )

    with patch("bifrost.integrations.get_client", return_value=client):
        assert await integrations.get("Missing") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [403, 424, 500, 503])
async def test_get_preserves_non_auth_api_failures(status_code: int) -> None:
    client = AsyncMock()
    client.post.return_value = _response(status_code)

    with patch("bifrost.integrations.get_client", return_value=client):
        with pytest.raises(BifrostAPIError) as exc_info:
            await integrations.get("HaloPSA")

    assert exc_info.value.response.status_code == status_code
    assert "safe failure" in str(exc_info.value)
    assert "must-not-appear" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_surfaces_unauthorized_as_authentication_error() -> None:
    client = AsyncMock()
    client.post.return_value = _response(401, "CLI session expired")

    with patch("bifrost.integrations.get_client", return_value=client):
        with pytest.raises(BifrostAuthenticationError, match="CLI session expired"):
            await integrations.get("HaloPSA")


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["list_mappings", "get_mapping"])
async def test_mapping_reads_preserve_api_failures(method_name: str) -> None:
    client = AsyncMock()
    client.post.return_value = _response(503)

    with patch("bifrost.integrations.get_client", return_value=client):
        with pytest.raises(BifrostAPIError):
            await getattr(integrations, method_name)("HaloPSA")
