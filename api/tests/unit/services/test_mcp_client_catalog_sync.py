"""Unit tests for external MCP tool-catalog schema preservation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_client.catalog_sync import sync_catalog


class _FakeDatabase:
    """Minimal catalog-sync persistence fake with no live-service dependency."""

    def __init__(self) -> None:
        self.rows: list[Any] = []
        self.commit = AsyncMock()

    async def execute(self, _statement: Any) -> SimpleNamespace:
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: self.rows)
        )

    def add(self, row: Any) -> None:
        self.rows.append(row)


class _CatalogSession:
    def __init__(self, tools: list[Any]) -> None:
        self._tools = tools

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=self._tools)


class _CatalogSessionContext:
    def __init__(self, tools: list[Any]) -> None:
        self._session = _CatalogSession(tools)

    async def __aenter__(self) -> _CatalogSession:
        return self._session

    async def __aexit__(self, *_args: object) -> None:
        return None


async def _sync_tool(tool: Any) -> Any:
    connection = SimpleNamespace(id=uuid4())
    db = _FakeDatabase()
    with (
        patch(
            "src.services.mcp_client.catalog_sync._resolve_service_token_for_sync",
            new=AsyncMock(return_value="service-token"),
        ),
        patch(
            "src.services.mcp_client.catalog_sync.mcp_client_session.open_session",
            return_value=_CatalogSessionContext([tool]),
        ),
    ):
        rows = await sync_catalog(connection, db)

    assert len(rows) == 1
    return rows[0]


@pytest.mark.asyncio
async def test_sync_catalog_preserves_advertised_output_schema() -> None:
    """The standard MCP ``outputSchema`` survives discovery without shaping."""
    output_schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {"$ref": "#/$defs/result"},
            }
        },
        "$defs": {"result": {"type": "object"}},
    }
    tool = SimpleNamespace(
        name="search_records",
        description="Search approved records.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
        outputSchema=output_schema,
    )

    catalog_row = await _sync_tool(tool)

    assert catalog_row.tool_schema == {
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        "description": "Search approved records.",
        "outputSchema": output_schema,
    }


@pytest.mark.asyncio
async def test_sync_catalog_does_not_invent_an_absent_output_schema() -> None:
    tool = SimpleNamespace(
        name="search_records",
        description=None,
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
        outputSchema=None,
    )

    catalog_row = await _sync_tool(tool)

    assert catalog_row.tool_schema == {
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    }
