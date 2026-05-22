"""Unit tests for bifrost.events SDK module."""

from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest


@pytest.fixture
def mock_client():
    """Return a mock BifrostClient with async post support."""
    client = MagicMock()
    client.post = AsyncMock()
    return client


@pytest.fixture
def mock_response_ok():
    return MagicMock(
        spec=httpx.Response,
        status_code=200,
        json=lambda: {"event_id": "abc-123", "subscribers_notified": 2},
    )


@pytest.mark.asyncio
async def test_emit_calls_correct_endpoint(mock_client, mock_response_ok):
    """events.emit sends POST to /api/events/emit with topic and data."""
    mock_client.post.return_value = mock_response_ok
    with patch("bifrost.events.get_client", return_value=mock_client), \
         patch("bifrost.events.raise_for_status_with_detail"), \
         patch("bifrost.events.resolve_scope", return_value=None):
        from bifrost.events import events
        result = await events.emit("acme.deal_won", {"amount": 50000})

    mock_client.post.assert_called_once_with(
        "/api/events/emit",
        json={"topic": "acme.deal_won", "data": {"amount": 50000}, "scope": None},
    )
    assert result == {"event_id": "abc-123", "subscribers_notified": 2}


@pytest.mark.asyncio
async def test_emit_scope_override(mock_client, mock_response_ok):
    """events.emit passes scope through resolve_scope."""
    mock_client.post.return_value = mock_response_ok
    org_uuid = "11111111-1111-1111-1111-111111111111"
    with patch("bifrost.events.get_client", return_value=mock_client), \
         patch("bifrost.events.raise_for_status_with_detail"), \
         patch("bifrost.events.resolve_scope", return_value=org_uuid) as mock_resolve:
        from bifrost.events import events
        await events.emit("acme.deal_won", {}, scope=org_uuid)

    mock_resolve.assert_called_once_with(org_uuid)
    _, kwargs = mock_client.post.call_args
    assert kwargs["json"]["scope"] == org_uuid


@pytest.mark.asyncio
async def test_emit_propagates_http_error(mock_client):
    """events.emit re-raises when raise_for_status_with_detail raises."""
    mock_response = MagicMock(spec=httpx.Response, status_code=400)
    mock_client.post.return_value = mock_response

    with patch("bifrost.events.get_client", return_value=mock_client), \
         patch("bifrost.events.raise_for_status_with_detail", side_effect=httpx.HTTPStatusError(
             "400", request=MagicMock(), response=mock_response
         )), \
         patch("bifrost.events.resolve_scope", return_value=None):
        from bifrost.events import events
        with pytest.raises(httpx.HTTPStatusError):
            await events.emit("acme.deal_won", {})


@pytest.mark.asyncio
async def test_emit_no_scope_resolves_context_scope(mock_client, mock_response_ok):
    """Omitting scope passes None to resolve_scope (uses execution context org)."""
    mock_client.post.return_value = mock_response_ok
    with patch("bifrost.events.get_client", return_value=mock_client), \
         patch("bifrost.events.raise_for_status_with_detail"), \
         patch("bifrost.events.resolve_scope", return_value="resolved-scope") as mock_resolve:
        from bifrost.events import events
        await events.emit("user.invited", {"user_id": "xyz"})

    mock_resolve.assert_called_once_with(None)
