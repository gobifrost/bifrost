"""
Unified documentation MCP tool — returns the llms.txt content.
"""

import logging
from typing import Any, Optional

from src.services.mcp_server.tool_result import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result

logger = logging.getLogger(__name__)


async def bifrost_get_docs(context: Any, sdk_module: Optional[str] = None) -> ToolResult:  # noqa: ARG001
    """Get platform docs or one Python SDK module's generated reference.

    Set sdk_module to a module name such as files, tables, or artifacts to keep
    its complete signatures within the agent's tool-result context budget.
    """
    from src.services.llms_txt import PYTHON_SDK_MODULES, generate_llms_txt, generate_sdk_module_docs

    if sdk_module is not None:
        if sdk_module not in PYTHON_SDK_MODULES:
            return error_result(
                f"Unknown Python SDK module: {sdk_module}",
                {"sdk_modules": list(PYTHON_SDK_MODULES)},
            )
        return success_result(
            f"Bifrost Python SDK: {sdk_module}",
            {"sdk_module": sdk_module, "schema": generate_sdk_module_docs(sdk_module)},
        )

    content = generate_llms_txt()
    return success_result("Bifrost platform documentation", {"schema": content})


TOOLS = [
    ("bifrost_get_docs", "Get Platform Docs", "Get Bifrost platform documentation, including live Python SDK signatures, inline Execute Code examples, workspace module discovery/imports, workflows, forms, agents, apps, tables, and events. Read before writing SDK code. Set sdk_module (e.g. files, tables, artifacts, events) to retrieve one complete Python SDK reference without truncation."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register docs tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {"bifrost_get_docs": bifrost_get_docs}
    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
