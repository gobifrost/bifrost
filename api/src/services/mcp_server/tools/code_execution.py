"""Inline Python execution through the existing REST execution boundary."""

from typing import Any, Optional

from src.services.mcp_server.tool_result import ToolResult, error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest


async def bifrost_execute_code(
    context: Any,
    code: str,
    input_data: Optional[dict] = None,
    script_name: str = "inline_script",
    org_id: Optional[str] = None,
) -> ToolResult:
    """Execute inline Python through the existing execution worker.

    Requires privileged Execute Code and trusted platform code. The existing
    worker is not a security sandbox for untrusted Python. SDK calls retain
    the execution identity's existing permissions and organization context;
    those checks do not contain arbitrary Python. No Workflow is registered. Returns
    immediately; retrieve the result using Get Execution. Agents should also
    have ``bifrost_execution_get`` assigned for result retrieval.
    """
    body: dict[str, Any] = {
        "code": code,
        "input_data": input_data or {},
        "script_name": script_name,
    }
    if org_id is not None:
        body["org_id"] = org_id
    status_code, payload = await call_rest(
        context, "POST", "/api/workflows/execute", json_body=body
    )
    if status_code != 200 or not isinstance(payload, dict):
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        return error_result(
            f"Execute Code failed: HTTP {status_code}: {detail}", {"body": payload}
        )
    return success_result(
        f"Queued execution {payload['execution_id']}. Use Get Execution to retrieve its result.",
        {**payload, "async": True},
    )


TOOLS = [
    (
        "bifrost_execute_code",
        "Execute Code",
        "Execute trusted inline Python without registering a Workflow. Requires privileged "
        "Execute Code and platform-administrator-level trust. The worker is not a security "
        "sandbox for untrusted Python. SDK requests retain existing permission and scope "
        "checks; those checks do not contain arbitrary Python. "
        "Call bifrost_get_docs before writing SDK code; "
        "use sdk_module for one complete module reference, e.g. files or tables. The docs "
        "also cover workspace module discovery/imports. Use top-level "
        "await for async calls, input_data keys as variables, and set result to the value "
        "to return. Returns an execution_id immediately; retrieve it with "
        "bifrost_get_execution on default MCP or bifrost_execution_get on an Agent. "
        "When configuring an Agent, assign bifrost_get_docs and bifrost_execution_get alongside "
        "this tool; for module discovery also assign list_content, bifrost_file_search, "
        "and read_content_lines. Repository reads still require repository.read.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(mcp, bifrost_execute_code, tool_id, description, get_context_fn)
