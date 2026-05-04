"""SDK auto-resolves created_by/updated_by from execution context."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from bifrost._context import _execution_context


def _get_tables_module():
    """Return the bifrost.tables module (not the tables class exported by bifrost)."""
    return sys.modules["bifrost.tables"]


@pytest.fixture
def fake_context():
    user_id = str(uuid4())
    ctx = MagicMock()
    ctx.user_id = user_id
    ctx.org_id = None
    ctx.organization = None
    token = _execution_context.set(ctx)
    yield ctx
    _execution_context.reset(token)


def _make_mock_client(response_data: dict) -> MagicMock:
    """Return a mock httpx client whose .post() returns a fake response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = response_data
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


@pytest.mark.asyncio
async def test_insert_attributes_to_context_user(fake_context, monkeypatch):
    from bifrost import tables as tables_class

    doc_response = {
        "id": "row-1",
        "table_id": str(uuid4()),
        "data": {"k": "v"},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": fake_context.user_id,
        "updated_by": None,
    }
    mock_client = _make_mock_client(doc_response)
    tables_mod = _get_tables_module()
    monkeypatch.setattr(tables_mod, "get_client", lambda: mock_client)

    await tables_class.insert("t1", {"k": "v"})

    mock_client.post.assert_called_once()
    call_json = mock_client.post.call_args[1]["json"]
    assert call_json["created_by"] == fake_context.user_id


@pytest.mark.asyncio
async def test_insert_explicit_override(fake_context, monkeypatch):
    from bifrost import tables as tables_class

    other_user = "other-user-uuid"
    doc_response = {
        "id": "row-1",
        "table_id": str(uuid4()),
        "data": {},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": other_user,
        "updated_by": None,
    }
    mock_client = _make_mock_client(doc_response)
    tables_mod = _get_tables_module()
    monkeypatch.setattr(tables_mod, "get_client", lambda: mock_client)

    await tables_class.insert("t1", {"k": "v"}, created_by=other_user)

    mock_client.post.assert_called_once()
    call_json = mock_client.post.call_args[1]["json"]
    assert call_json["created_by"] == other_user
