"""
Unit tests for the /api/llms.txt endpoint.

Tests the unified docs generation by calling the MCP tool directly.
"""

import pytest


@pytest.mark.asyncio
async def test_get_docs_returns_markdown():
    from src.services.mcp_server.tools.docs import bifrost_get_docs

    result = await bifrost_get_docs(context=None)
    schema = result.structured_content["schema"]
    assert isinstance(schema, str)
    assert len(schema) > 100
    assert "# Bifrost Platform" in schema


@pytest.mark.asyncio
async def test_get_docs_contains_all_sections():
    from src.services.mcp_server.tools.docs import bifrost_get_docs

    result = await bifrost_get_docs(context=None)
    schema = result.structured_content["schema"]

    # Verify all major sections are present
    assert "## Workflows & Tools" in schema
    assert "## Forms" in schema
    assert "## Agents" in schema
    assert "## Apps" in schema
    assert "## Tables" in schema
    assert "## Events" in schema
