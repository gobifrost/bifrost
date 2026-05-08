"""
Bifrost MCP Server

MCP server for Bifrost platform capabilities using FastMCP for HTTP access.

Architecture:
    - MCPContext: Holds user/org context for permission-scoped tool execution
    - BifrostMCPServer: Creates MCP servers with registered tools
    - Uses FastMCP for HTTP access (Claude Desktop, etc.)

Usage:
    # For external access (FastMCP HTTP)
    server = BifrostMCPServer(context)
    fastmcp_server = server.get_fastmcp_server()
    app = fastmcp_server.http_app()
"""

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastmcp.tools import ToolResult

if TYPE_CHECKING:
    from fastmcp import FastMCP

from src.services.mcp_server.tools import TOOL_MODULES, register_all_tools
from src.services.mcp_server.tool_result import error_result, success_result

logger = logging.getLogger(__name__)

# FastMCP for HTTP access - runtime import check
HAS_FASTMCP = False
_FastMCP: type["FastMCP"] | None = None
_Icon: type | None = None

try:
    from fastmcp import FastMCP as _FastMCPClass
    from mcp.types import Icon as _IconClass

    _FastMCP = _FastMCPClass
    _Icon = _IconClass
    HAS_FASTMCP = True
except ImportError as e:
    # fastmcp / mcp packages are optional; HTTP MCP support stays disabled
    logger.debug(f"fastmcp not available, MCP HTTP server disabled: {e}")

# Bifrost branding
BIFROST_ICON_URL = "https://bifrostintegrations.blob.core.windows.net/public/logo.svg"
BIFROST_WEBSITE_URL = "https://docs.gobifrost.com"


# =============================================================================
# Workflow Tool Name Mapping
# =============================================================================

# Workflow tools are registered with normalized names for MCP compatibility.
# These mappings track the relationship between tool names and workflow UUIDs.

# Forward mapping: normalized tool_name -> workflow_id
_TOOL_NAME_TO_WORKFLOW_ID: dict[str, str] = {}
# Reverse mapping: workflow_id -> tool_name
_WORKFLOW_ID_TO_TOOL_NAME: dict[str, str] = {}

# Stored references for refresh_workflow_tools()
_fastmcp_instance: "FastMCP | None" = None
_default_mcp_context: "MCPContext | None" = None


def _normalize_tool_name(name: str) -> str:
    """
    Convert workflow name to valid MCP tool name (snake_case).

    Examples:
        "Review Tickets" -> "review_tickets"
        "get-user-data" -> "get_user_data"
        "ProcessOrder123" -> "processorder123"
    """
    import re

    name = name.lower().strip()
    # Replace spaces, hyphens, and multiple underscores with single underscore
    name = re.sub(r"[\s\-]+", "_", name)
    # Remove any non-alphanumeric characters except underscores
    name = re.sub(r"[^a-z0-9_]", "", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)
    # Remove leading/trailing underscores
    name = name.strip("_")
    return name


def _generate_short_suffix(length: int = 3) -> str:
    """Generate a short random alphanumeric suffix for duplicate tool names."""
    import secrets
    import string

    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def get_workflow_id_for_tool(tool_name: str) -> str | None:
    """
    Get workflow UUID for a registered MCP tool name.

    Args:
        tool_name: The MCP tool name (e.g., "review_tickets")

    Returns:
        Workflow UUID string or None if not found
    """
    return _TOOL_NAME_TO_WORKFLOW_ID.get(tool_name)


def get_registered_tool_name(workflow_id: str) -> str | None:
    """
    Get the registered MCP tool name for a workflow ID.

    Args:
        workflow_id: The workflow UUID string

    Returns:
        Tool name string or None if not registered
    """
    return _WORKFLOW_ID_TO_TOOL_NAME.get(workflow_id)


@dataclass
class MCPContext:
    """
    Context for MCP tool execution.

    Provides user and organization scope for permission-aware tool execution.
    All MCP tools receive this context to enforce access control.
    """

    user_id: UUID | str
    org_id: UUID | str | None = None
    is_platform_admin: bool = False
    user_email: str = ""
    user_name: str = ""

    # System tools enabled for this context (from agent.system_tools)
    enabled_system_tools: list[str] = field(default_factory=list)

    # Knowledge namespaces accessible to this user (from agent.knowledge_sources)
    accessible_namespaces: list[str] = field(default_factory=list)

    # Database session from executor context (None when running via MCP server)
    session: Any = None

    def __post_init__(self) -> None:
        # JWT claims arrive as strings; downstream comparisons (e.g. against
        # ORM UUID columns) silently fail because `UUID == str` is False.
        # Normalize once at the boundary so org-scoped repos see real UUIDs.
        if isinstance(self.user_id, str) and self.user_id:
            self.user_id = UUID(self.user_id)
        if isinstance(self.org_id, str) and self.org_id:
            self.org_id = UUID(self.org_id)


# =============================================================================
# Context Helper Functions (for FastMCP authentication)
# =============================================================================


def _get_agent_id_from_scope() -> UUID | None:
    """Read the agent UUID written to the ASGI scope by AgentScopeMCPMiddleware.

    Returns ``None`` outside an HTTP request context (e.g. stdio MCP) or when
    the request is to the un-scoped /mcp mount.
    """
    from fastmcp.server.dependencies import get_http_request

    try:
        request = get_http_request()
        agent_id_str = request.scope.get("mcp_agent_id")
        if agent_id_str:
            return UUID(agent_id_str)
    except (RuntimeError, ValueError, AttributeError) as e:
        # RuntimeError: not in HTTP context; ValueError: bad UUID; AttributeError:
        # scope shape unexpected. None of these should crash tool execution.
        logger.debug(f"could not extract agent_id from scope: {e}")
    return None


def _get_context_from_token() -> MCPContext:
    """
    Get MCPContext from authenticated FastMCP token.

    This extracts user information from the validated JWT token set by
    FastMCP's authentication middleware. Used by tool execution to get
    the actual authenticated user instead of the default startup context.

    Returns:
        MCPContext populated with authenticated user's information

    Raises:
        ToolError: If no authenticated user (token missing or invalid)
    """
    from fastmcp.exceptions import ToolError
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is None:
        raise ToolError("Authentication required")

    return MCPContext(
        user_id=token.claims.get("user_id", ""),
        org_id=token.claims.get("org_id"),
        is_platform_admin=token.claims.get("is_superuser", False),
        user_email=token.claims.get("email", ""),
        user_name=token.claims.get("name", ""),
    )


async def _get_runtime_context() -> MCPContext:
    """Build the per-request MCPContext used by tool execution.

    Populates ``accessible_namespaces`` according to the ASGI mount:

    - ``/mcp/{agent_id}`` (agent-scoped): namespaces == that agent's
      ``knowledge_sources`` only. Cross-namespace requests are rejected
      by the tool itself, so a session bound to an agent can only see
      that agent's knowledge.
    - ``/mcp`` (un-scoped): namespaces == union of the user's accessible
      agents' ``knowledge_sources``.
    """
    from fastmcp.exceptions import ToolError
    from fastmcp.server.dependencies import get_access_token

    from src.core.database import get_db_context
    from src.services.mcp_server.tool_access import MCPToolAccessService

    token = get_access_token()
    if token is None:
        raise ToolError("Authentication required")

    user_roles = token.claims.get("roles", [])
    is_superuser = token.claims.get("is_superuser", False)
    user_id = token.claims.get("user_id")
    org_id = token.claims.get("org_id")
    agent_id = _get_agent_id_from_scope()

    accessible_namespaces: list[str] = []
    try:
        async with get_db_context() as db:
            service = MCPToolAccessService(db)
            if agent_id is not None:
                agent_result = await service.get_tools_for_agent(
                    agent_id=agent_id,
                    user_roles=user_roles,
                    is_superuser=is_superuser,
                    user_id=user_id,
                    org_id=org_id,
                )
                if agent_result is not None:
                    accessible_namespaces = list(agent_result.accessible_namespaces)
            else:
                result = await service.get_accessible_tools(
                    user_roles=user_roles,
                    is_superuser=is_superuser,
                    user_id=user_id,
                    org_id=org_id,
                )
                accessible_namespaces = list(result.accessible_namespaces)
    except Exception as e:
        logger.warning(f"Failed to resolve accessible namespaces: {e}")

    return MCPContext(
        user_id=token.claims.get("user_id", ""),
        org_id=token.claims.get("org_id"),
        is_platform_admin=is_superuser,
        user_email=token.claims.get("email", ""),
        user_name=token.claims.get("name", ""),
        accessible_namespaces=accessible_namespaces,
    )


# =============================================================================
# BifrostMCPServer
# =============================================================================


class BifrostMCPServer:
    """
    Bifrost MCP Server using FastMCP.

    Creates MCP servers with tools registered based on user context and
    permissions. Uses FastMCP for HTTP access (Claude Desktop, etc.).

    Usage:
        # Create server with context
        context = MCPContext(user_id=user.id, org_id=user.org_id)
        server = BifrostMCPServer(context)

        # For FastMCP HTTP use
        fastmcp_server = server.get_fastmcp_server()
    """

    def __init__(
        self,
        context: MCPContext,
        *,
        name: str = "bifrost",
    ):
        """
        Initialize Bifrost MCP server.

        Args:
            context: MCP context with user/org information
            name: Server name (default: "bifrost")
        """
        self.context = context
        self._name = name

        # Determine enabled tools
        self._enabled_tools: set[str] | None = None
        if context.enabled_system_tools:
            self._enabled_tools = set(context.enabled_system_tools)

        # FastMCP server (lazy initialized)
        self._fastmcp: Any = None

    def get_fastmcp_server(self, auth: Any = None) -> "FastMCP":
        """
        Get FastMCP server for HTTP access.

        The server is cached for reuse. If auth is provided, a new server
        is created with authentication enabled.

        Args:
            auth: Optional authentication provider (e.g., token verifier).
                  If provided, creates a new server with auth.

        Returns:
            FastMCP server instance
        """
        if not HAS_FASTMCP:
            raise ImportError(
                "fastmcp is required for MCP access. "
                "Install it with: pip install 'fastmcp>=3.2.0,<4'"
            )

        # Build icon list for branding
        icons = []
        if _Icon is not None:
            icons = [
                _Icon(
                    src=BIFROST_ICON_URL,
                    mimeType="image/svg+xml",
                    sizes=["any"],
                )
            ]

        # Per-request context: pull user from the JWT and scope namespaces by
        # the ASGI mount (agent-scoped vs. union). Falls back to the server's
        # default_context only when there is no FastMCP request context at all
        # (e.g. tool introspection at startup); other failures (auth required,
        # DB error) are real errors and must not silently degrade to the
        # default admin context.
        default_context = self.context

        async def get_context_fn() -> MCPContext:
            from fastmcp.exceptions import ToolError

            try:
                return await _get_runtime_context()
            except ToolError:
                # ToolError is raised when no auth token exists in the
                # current call — for HTTP requests that means unauthenticated
                # and should bubble up; for tool introspection at startup
                # there is no HTTP context, so fall back to default.
                return default_context

        # If auth is provided, always create a new server with auth
        if auth is not None:
            assert _FastMCP is not None
            mcp = _FastMCP(
                self._name,
                auth=auth,
                website_url=BIFROST_WEBSITE_URL,
                icons=icons,
            )
            register_all_tools(mcp, get_context_fn)
            tool_count = sum(len(m.TOOLS) for m in TOOL_MODULES)
            logger.info(f"Created FastMCP server with {tool_count} tools and auth")
            return mcp

        # Otherwise use cached server
        if self._fastmcp is None:
            assert _FastMCP is not None
            self._fastmcp = _FastMCP(
                self._name,
                website_url=BIFROST_WEBSITE_URL,
                icons=icons,
            )
            register_all_tools(self._fastmcp, get_context_fn)
            tool_count = sum(len(m.TOOLS) for m in TOOL_MODULES)
            logger.info(f"Created FastMCP server with {tool_count} tools")
        return self._fastmcp

    def get_tool_names(self) -> list[str]:
        """Get list of registered tool names (prefixed for SDK use)."""
        all_tools = [tool_id for m in TOOL_MODULES for tool_id, _, _ in m.TOOLS]
        if self._enabled_tools:
            tools = [t for t in all_tools if t in self._enabled_tools]
        else:
            tools = all_tools
        return [f"mcp__{self._name}__{t}" for t in tools]


def get_system_tools() -> list[dict[str, Any]]:
    """
    Get system tool metadata from tool modules.

    Returns list of dicts with id, name, description, and parameters for each tool.
    Used by /api/tools endpoint and agent executor for LLM tool definitions.
    """
    import inspect
    from typing import get_origin, get_args, Union

    def python_type_to_json_schema(annotation: Any) -> dict[str, Any]:
        """Convert Python type annotation to JSON Schema."""
        if annotation is inspect.Parameter.empty or annotation is None:
            return {"type": "string"}

        # Handle Optional types (Union[X, None])
        origin = get_origin(annotation)
        if origin is Union:
            args = [a for a in get_args(annotation) if a is not type(None)]
            if len(args) == 1:
                return python_type_to_json_schema(args[0])

        # Basic type mappings
        if annotation is str:
            return {"type": "string"}
        elif annotation is int:
            return {"type": "integer"}
        elif annotation is float:
            return {"type": "number"}
        elif annotation is bool:
            return {"type": "boolean"}
        elif annotation is dict or origin is dict:
            schema: dict[str, Any] = {"type": "object"}
            if origin is dict:
                args = get_args(annotation)
                if len(args) == 2:
                    schema["additionalProperties"] = python_type_to_json_schema(args[1])
            else:
                schema["additionalProperties"] = True
            return schema
        elif annotation is list or origin is list:
            return {"type": "array"}

        return {"type": "string"}

    tools = []
    for module in TOOL_MODULES:
        if hasattr(module, "TOOLS"):
            # Build a mapping of tool_id -> function for this module
            tool_funcs: dict[str, Any] = {}
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if callable(attr) and not attr_name.startswith("_"):
                    tool_funcs[attr_name] = attr

            for tool_id, name, description in module.TOOLS:
                # Get the function and extract parameters
                func = tool_funcs.get(tool_id)
                parameters: dict[str, Any] = {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                }

                if func:
                    sig = inspect.signature(func)
                    params = list(sig.parameters.items())
                    # Skip first param (context)
                    for param_name, param in params[1:]:
                        prop = python_type_to_json_schema(param.annotation)
                        parameters["properties"][param_name] = prop
                        # Required if no default value
                        if param.default is inspect.Parameter.empty:
                            parameters["required"].append(param_name)

                tools.append({
                    "id": tool_id,
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                })
    return tools


def get_system_tool_function(tool_id: str) -> Any | None:
    """
    Get the callable function for a system tool by ID.

    Args:
        tool_id: The tool ID (e.g., "execute_workflow", "list_agents")

    Returns:
        The async callable function, or None if not found.
    """
    for module in TOOL_MODULES:
        if hasattr(module, "TOOLS"):
            # Check if this tool is in this module
            tool_ids_in_module = [t[0] for t in module.TOOLS]
            if tool_id in tool_ids_in_module:
                # Get the function from the module
                if hasattr(module, tool_id):
                    return getattr(module, tool_id)
    return None


# =============================================================================
# Factory Function
# =============================================================================


async def create_user_mcp_server(
    user_id: UUID | str,
    org_id: UUID | str | None = None,
    is_platform_admin: bool = False,
    enabled_tools: list[str] | None = None,
    user_email: str = "",
    user_name: str = "",
) -> BifrostMCPServer:
    """
    Create an MCP server scoped to a user's permissions.

    Args:
        user_id: User ID
        org_id: Organization ID (optional)
        is_platform_admin: Whether user is platform admin
        enabled_tools: List of enabled tool IDs (None = all)
        user_email: User email for context
        user_name: User name for context

    Returns:
        BifrostMCPServer configured for this user
    """
    context = MCPContext(
        user_id=user_id,
        org_id=org_id,
        is_platform_admin=is_platform_admin,
        enabled_system_tools=enabled_tools or [],
        user_email=user_email,
        user_name=user_name,
    )
    return BifrostMCPServer(context)


# =============================================================================
# Workflow Tools (Dynamic from Database)
# =============================================================================

# WorkflowTool class for FastMCP - wraps workflow execution
_WorkflowTool: type | None = None

if HAS_FASTMCP:
    from fastmcp.tools import Tool as _FastMCPTool

    class WorkflowTool(_FastMCPTool):
        """
        MCP Tool that executes a Bifrost workflow.

        Subclasses FastMCP's Tool to:
        1. Accept JSON Schema directly via `parameters` field
        2. Override `run()` to delegate to workflow execution

        This bypasses FastMCP's function signature inspection, allowing
        dynamic parameter schemas from workflow `parameters_schema`.

        The execution context is retrieved dynamically from the authenticated
        token at runtime via _get_context_from_token().

        Results are auto-wrapped in ToolResult:
        - If result is already ToolResult, pass through
        - If result is a JSON string, parse and wrap
        - If result has {"error": ...}, return error_result
        - Otherwise format as display text with structured data
        """

        workflow_id: str
        workflow_name: str

        model_config = {"arbitrary_types_allowed": True}

        async def run(self, arguments: dict[str, Any]) -> ToolResult:
            """Execute the workflow and wrap result in ToolResult."""
            try:
                context = _get_context_from_token()
            except Exception as e:
                return error_result(f"Authentication error: {e}")

            try:
                result = await _execute_workflow_tool_impl(
                    context,
                    self.workflow_id,
                    self.workflow_name,
                    **arguments,
                )

                # If user returned ToolResult, pass through unchanged
                if isinstance(result, ToolResult):
                    return result

                # Parse JSON string if needed (legacy workflow returns)
                if isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                        result = parsed
                    except json.JSONDecodeError:
                        # Plain string result - return as success
                        return success_result(result, {"result": result})

                # Auto-wrap dict results
                if isinstance(result, dict):
                    if result.get("error"):
                        return error_result(str(result["error"]), result)
                    # Format as pretty JSON for display
                    display = json.dumps(result, indent=2, default=str)
                    return success_result(display, result)

                # Fallback for other types
                display = str(result)
                return success_result(display, {"result": result})

            except Exception as e:
                logger.exception(f"Error executing workflow tool {self.workflow_name}: {e}")
                return error_result(str(e))

    _WorkflowTool = WorkflowTool


def _map_type_to_json_schema(param_type: str) -> str:
    """Map workflow parameter type to JSON Schema type."""
    type_map = {
        "string": "string",
        "str": "string",
        "int": "integer",
        "integer": "integer",
        "float": "number",
        "number": "number",
        "bool": "boolean",
        "boolean": "boolean",
        "json": "object",
        "dict": "object",
        "object": "object",
        "list": "array",
        "array": "array",
    }
    return type_map.get(param_type.lower(), "string")


async def _execute_workflow_tool_impl(
    context: MCPContext,
    workflow_id: str,
    workflow_name: str,
    **inputs: Any,
) -> Any:
    """Execute a specific workflow tool by ID."""
    from src.core.database import get_db_context
    from src.repositories.workflows import WorkflowRepository
    from src.services.execution.service import execute_tool

    try:
        async with get_db_context() as db:
            # Use context for proper org scoping and role-based access
            repo = WorkflowRepository(
                db,
                org_id=context.org_id,
                user_id=context.user_id,
                is_superuser=context.is_platform_admin,
            )
            workflow = await repo.get(id=workflow_id)

            if not workflow:
                return f"Error: Workflow '{workflow_name}' not found"

            if not workflow.is_active:
                return f"Error: Workflow '{workflow_name}' is not active"

            # Execute the workflow
            result = await execute_tool(
                workflow_id=str(workflow.id),
                workflow_name=workflow.name,
                parameters=inputs,
                user_id=str(context.user_id),
                user_email=context.user_email,
                user_name=context.user_name or "MCP User",
                org_id=str(context.org_id) if context.org_id else None,
                is_platform_admin=context.is_platform_admin,
            )

            success = result.status.value == "Success"
            if success:
                return result.result
            else:
                return f"Error: {result.error}"

    except Exception as e:
        logger.exception(f"Error executing workflow tool {workflow_name}: {e}")
        return f"Error executing workflow: {e}"


async def _notify_duplicate_workflow_names(duplicates: dict[str, list]) -> None:
    """Log warning about duplicate workflow names."""
    for name, workflows in duplicates.items():
        workflow_names = [w.name for w in workflows]
        logger.warning(
            f"Multiple workflows normalize to '{name}': {workflow_names}. "
            "Consider renaming to avoid confusion."
        )


async def _register_workflow_tools(mcp: "FastMCP", context: MCPContext) -> int:
    """
    Register workflow tools with FastMCP server using human-readable names.

    Creates WorkflowTool instances for each workflow with is_tool=True,
    passing the parameters_schema directly as JSON Schema. This bypasses
    FastMCP's function signature inspection.

    Tool names are normalized from workflow names (e.g., "Review Tickets" -> "review_tickets").
    Duplicate names get a short random suffix (e.g., "review_tickets_x7k").

    Returns:
        Number of workflow tools registered
    """
    global _TOOL_NAME_TO_WORKFLOW_ID, _WORKFLOW_ID_TO_TOOL_NAME
    global _fastmcp_instance, _default_mcp_context

    # Store refs so refresh_workflow_tools() can re-call us
    _fastmcp_instance = mcp
    _default_mcp_context = context

    if not HAS_FASTMCP or _WorkflowTool is None:
        logger.warning("FastMCP not available, skipping workflow tool registration")
        return 0

    from src.core.database import get_db_context
    from src.services.tool_registry import ToolRegistry

    try:
        async with get_db_context() as db:
            registry = ToolRegistry(db)
            tools = await registry.get_all_tools()

            # Clear previous mappings (in case of re-registration)
            _TOOL_NAME_TO_WORKFLOW_ID = {}
            _WORKFLOW_ID_TO_TOOL_NAME = {}

            # Group workflows by normalized name to detect duplicates
            name_groups: dict[str, list] = {}
            for tool in tools:
                normalized = _normalize_tool_name(tool.name)
                # Handle edge case: empty normalized name falls back to workflow ID
                if not normalized:
                    normalized = str(tool.id)
                name_groups.setdefault(normalized, []).append(tool)

            # Detect duplicates and notify admins
            duplicates = {name: wfs for name, wfs in name_groups.items() if len(wfs) > 1}
            if duplicates:
                await _notify_duplicate_workflow_names(duplicates)
                logger.warning(
                    f"Found {len(duplicates)} duplicate workflow names: "
                    f"{list(duplicates.keys())}"
                )

            # Get native tool names to avoid collisions
            native_tool_names = {tool_id for m in TOOL_MODULES for tool_id, _, _ in m.TOOLS}

            # Assign unique tool names and register
            count = 0
            for base_name, workflows in name_groups.items():
                for i, tool in enumerate(workflows):
                    workflow_id = str(tool.id)
                    workflow_name = tool.name
                    description = tool.description or f"Execute the {workflow_name} workflow"

                    # Avoid collision with native tools by adding "_workflow" suffix
                    # For duplicates among workflows, add random suffix
                    if base_name in native_tool_names:
                        # Collides with native tool - add "_workflow" suffix
                        if i == 0:
                            tool_name = f"{base_name}_workflow"
                        else:
                            tool_name = f"{base_name}_workflow_{_generate_short_suffix()}"
                    elif i == 0:
                        tool_name = base_name
                    else:
                        tool_name = f"{base_name}_{_generate_short_suffix()}"

                    # Store mapping for middleware lookups
                    _TOOL_NAME_TO_WORKFLOW_ID[tool_name] = workflow_id
                    _WORKFLOW_ID_TO_TOOL_NAME[workflow_id] = tool_name

                    # Build JSON Schema from parameters_schema
                    properties: dict[str, Any] = {}
                    required: list[str] = []
                    for param in tool.parameters_schema:
                        param_name = param.get("name")
                        if not param_name:
                            continue

                        param_type = param.get("type", "string")
                        json_type = _map_type_to_json_schema(param_type)

                        properties[param_name] = {
                            "type": json_type,
                            "description": param.get("label") or param.get("description") or param_name,
                        }

                        if param.get("required", False):
                            required.append(param_name)

                    # Create WorkflowTool with human-readable name
                    # Context is retrieved dynamically from authenticated token at runtime
                    workflow_tool = _WorkflowTool(
                        name=tool_name,  # Human-readable name instead of UUID
                        description=description,
                        workflow_id=workflow_id,
                        workflow_name=workflow_name,
                        parameters={
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    )

                    # Add to FastMCP server
                    try:
                        mcp.add_tool(workflow_tool)
                        count += 1
                        logger.debug(
                            f"Registered workflow tool: {tool_name} "
                            f"(workflow: {workflow_name}, id: {workflow_id})"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to register workflow tool {workflow_name}: {e}")

            logger.info(f"Registered {count} workflow tools with FastMCP")
            return count

    except Exception as e:
        logger.exception(f"Error registering workflow tools: {e}")
        return 0


async def refresh_workflow_tools() -> int:
    """Re-register all workflow tools from DB. Removes stale tools.

    Call this after workflow create/update/delete to keep MCP tool list current.
    """
    if not _fastmcp_instance or not _default_mcp_context:
        logger.debug("MCP not initialized, skipping workflow tool refresh")
        return 0

    old_tool_names = set(_TOOL_NAME_TO_WORKFLOW_ID.keys())
    count = await _register_workflow_tools(_fastmcp_instance, _default_mcp_context)
    new_tool_names = set(_TOOL_NAME_TO_WORKFLOW_ID.keys())

    # Remove tools that no longer exist from FastMCP
    for stale_name in old_tool_names - new_tool_names:
        try:
            _fastmcp_instance._tool_manager.remove_tool(stale_name)
            logger.debug(f"Removed stale MCP tool: {stale_name}")
        except Exception:
            pass  # Tool already gone

    added = new_tool_names - old_tool_names
    removed = old_tool_names - new_tool_names
    if added or removed:
        logger.info(
            f"MCP workflow tools refreshed: {count} total, "
            f"+{len(added)} added, -{len(removed)} removed"
        )

    return count
