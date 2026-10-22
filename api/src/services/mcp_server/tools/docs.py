"""
Unified documentation MCP tool — returns the llms.txt content.
"""

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import success_result

logger = logging.getLogger(__name__)


async def get_docs(context: Any) -> ToolResult:  # noqa: ARG001
    """Get complete Bifrost platform documentation — SDK, forms, agents, apps, tables, events."""
    from src.services.llms_txt import generate_llms_txt

    content = generate_llms_txt()
    return success_result("Bifrost platform documentation", {"schema": content})


TOOLS = [
    ("get_docs", "Get Platform Docs", "Get complete Bifrost platform documentation covering workflows, forms, agents, apps, tables, and events."),
]
