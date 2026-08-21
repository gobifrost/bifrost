"""
Data Provider MCP Tools

Tools for getting schema documentation for data providers.
Note: Data providers are now stored in the workflows table with type='data_provider'.
Use bifrost_list_workflows to see data providers.
"""

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import success_result

logger = logging.getLogger(__name__)


async def get_data_provider_schema(context: Any) -> ToolResult:  # noqa: ARG001
    """Get documentation about data provider structure and decorators generated from Pydantic models."""
    from src.models.contracts.workflows import DataProviderMetadata, WorkflowParameter
    from src.services.mcp_server.schema_utils import models_to_markdown

    # Generate model documentation
    model_docs = models_to_markdown([
        (DataProviderMetadata, "DataProviderMetadata"),
        (WorkflowParameter, "WorkflowParameter (input parameters)"),
    ], "Data Provider Schema Documentation")

    # Data provider-specific documentation
    usage_docs = """
## Return Format

Data providers must return a list of objects with label/value pairs:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| label | string | Yes | Text shown to user in dropdown |
| value | string | Yes | Value stored when selected |
| metadata | object | No | Optional extra data for workflows |

## Using in Forms

Reference a data provider in form field definitions:

```json
{
  "name": "customer",
  "type": "select",
  "label": "Select Customer",
  "data_provider_id": "uuid-of-provider",
  "data_provider_inputs": {
    "department_id": "{{department}}"
  }
}
```

## Viewing Data Providers

Data providers are stored as workflows with type='data_provider'.
Use `bifrost_list_workflows` with the type filter to see all available data providers.

## SDK Documentation

For `@data_provider` decorator documentation and examples, use `get_sdk_schema`.
"""

    schema_doc = model_docs + usage_docs
    return success_result("Data provider schema documentation", {"schema": schema_doc})


# Tool metadata for registration
TOOLS: list[tuple[str, str, str]] = []


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all data_providers tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs: dict[str, Any] = {}

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
