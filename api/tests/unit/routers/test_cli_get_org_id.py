"""Unit tests for ``_get_cli_org_id`` scope validation.

The function used to honor any scope string from any caller — including
malformed input that flowed into raw SQL and surfaced as an asyncpg
``InvalidTextRepresentation`` 500. It now validates that ``scope`` is
either the literal ``"global"``, a valid UUID, or null/absent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.cli import _get_cli_org_id


@pytest.mark.asyncio
async def test_global_returns_none():
    """``scope='global'`` short-circuits to ``None`` without DB access."""
    db = AsyncMock()
    result = await _get_cli_org_id(uuid4(), "global", db)
    assert result is None
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_valid_uuid_passthrough():
    """A valid UUID scope is returned verbatim."""
    db = AsyncMock()
    target = str(uuid4())
    result = await _get_cli_org_id(uuid4(), target, db)
    assert result == target
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_garbage_scope_raises_422():
    """Non-UUID, non-'global' scope raises 422 (not a downstream 500)."""
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await _get_cli_org_id(uuid4(), "not-a-uuid", db)
    assert exc.value.status_code == 422
    assert "uuid" in exc.value.detail.lower() or "scope" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_none_scope_uses_developer_context():
    """``scope=None`` falls back to the user's DeveloperContext default."""
    expected = uuid4()

    class FakeDevCtx:
        default_org_id = expected

    class FakeResult:
        def scalar_one_or_none(self):
            return FakeDevCtx()

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult())

    result = await _get_cli_org_id(uuid4(), None, db)
    assert result == str(expected)


@pytest.mark.asyncio
async def test_none_scope_no_developer_context_returns_none():
    """``scope=None`` with no DeveloperContext returns ``None`` (global)."""
    class FakeResult:
        def scalar_one_or_none(self):
            return None

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult())

    result = await _get_cli_org_id(uuid4(), None, db)
    assert result is None


@pytest.mark.asyncio
async def test_uppercase_uuid_accepted():
    """UUID validation is case-insensitive (Python's ``UUID`` parses both)."""
    db = AsyncMock()
    upper = str(uuid4()).upper()
    result = await _get_cli_org_id(uuid4(), upper, db)
    assert result == upper


@pytest.mark.asyncio
async def test_empty_string_treated_as_none():
    """Empty string scope is falsy → falls back to DeveloperContext lookup."""
    class FakeResult:
        def scalar_one_or_none(self):
            return None

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult())

    result = await _get_cli_org_id(uuid4(), "", db)
    assert result is None
