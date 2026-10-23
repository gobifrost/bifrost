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

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from src.core.principal import UserPrincipal

from src.services.mcp_server.agent_scope import (
    get_scoped_agent_id as _get_agent_id_from_scope,
)
from src.services.mcp_server.tools import (
    TOOL_MODULES,
    register_gateway_tools,
)
from src.services.mcp_server.tools.gateway import (
    GATEWAY_INSTRUCTIONS,
    GATEWAY_TOOL_NAMES,
)

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
    is_provider_org: bool = False
    # External account flag, preserved from User.is_external at token mint.
    is_external: bool = False
    user_email: str = ""
    user_name: str = ""
    # Who an agent run is for (its run lineage). The REST bridge carries it as
    # a token claim so child runs and access checks see the agent run's user.
    run_user_id: UUID | str | None = None
    # Audit attribution only; never used for authorization.
    agent_run_id: UUID | str | None = None
    # Restricts the external MCP gateway to this agent's approved graph.
    scoped_agent_id: UUID | None = None
    # A durable runtime credential to forward through the REST bridge. These
    # are identifiers only; HTTP auth resolves their authority from the DB.
    runtime_credential_kind: str | None = None
    runtime_credential_id: UUID | str | None = None
    runtime_service_id: UUID | str | None = None

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
        if isinstance(self.run_user_id, str) and self.run_user_id:
            self.run_user_id = UUID(self.run_user_id)
        if isinstance(self.agent_run_id, str) and self.agent_run_id:
            self.agent_run_id = UUID(self.agent_run_id)
        if isinstance(self.runtime_credential_id, str) and self.runtime_credential_id:
            self.runtime_credential_id = UUID(self.runtime_credential_id)
        if isinstance(self.runtime_service_id, str) and self.runtime_service_id:
            self.runtime_service_id = UUID(self.runtime_service_id)



# =============================================================================
# Context Helper Functions (for FastMCP authentication)
# =============================================================================


def context_from_principal(principal: "UserPrincipal") -> MCPContext:
    """Carry verified identity and durable runtime provenance into MCP."""
    runtime = principal.runtime_authority
    return MCPContext(
        user_id=principal.user_id,
        org_id=principal.organization_id,
        is_platform_admin=principal.is_superuser,
        is_provider_org=principal.is_provider_org,
        is_external=principal.is_external,
        user_email=principal.email,
        user_name=principal.name,
        run_user_id=principal.run_user_id,
        runtime_credential_kind=None if runtime is None else runtime.kind,
        runtime_credential_id=None if runtime is None else runtime.credential_id,
        runtime_service_id=None if runtime is None else runtime.service_id,
        agent_run_id=(
            runtime.credential_id if runtime is not None and runtime.kind == "agent_run" else None
        ),
    )


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
        is_provider_org=token.claims.get("is_provider_org", False),
        is_external=token.claims.get("is_external", False),
        user_email=token.claims.get("email", ""),
        user_name=token.claims.get("name", ""),
        runtime_credential_kind=token.claims.get("runtime_credential_kind"),
        runtime_credential_id=token.claims.get("runtime_credential_id"),
        runtime_service_id=token.claims.get("runtime_service_id"),
    )


async def _get_runtime_context() -> MCPContext:
    """Build the per-request MCPContext used by tool execution.

    The live gateway resolves its selected Agent and tool graph for every
    dispatch. This request context deliberately carries no precomputed tool
    or knowledge namespace inventory.
    """
    from fastmcp.exceptions import ToolError
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is None:
        raise ToolError("Authentication required")

    is_superuser = token.claims.get("is_superuser", False)
    is_provider_org = token.claims.get("is_provider_org", False)
    is_external = token.claims.get("is_external", False)
    agent_id = _get_agent_id_from_scope()

    return MCPContext(
        user_id=token.claims.get("user_id", ""),
        org_id=token.claims.get("org_id"),
        is_platform_admin=is_superuser,
        is_provider_org=is_provider_org,
        is_external=is_external,
        user_email=token.claims.get("email", ""),
        user_name=token.claims.get("name", ""),
        runtime_credential_kind=token.claims.get("runtime_credential_kind"),
        runtime_credential_id=token.claims.get("runtime_credential_id"),
        runtime_service_id=token.claims.get("runtime_service_id"),
        scoped_agent_id=agent_id,
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

        async def get_context_fn() -> MCPContext:
            return await _get_runtime_context()

        # If auth is provided, always create a new server with auth
        if auth is not None:
            assert _FastMCP is not None
            mcp = _FastMCP(
                self._name,
                auth=auth,
                instructions=GATEWAY_INSTRUCTIONS,
                website_url=BIFROST_WEBSITE_URL,
                icons=icons,
            )
            register_gateway_tools(mcp, get_context_fn)
            logger.info("Created FastMCP server with %s gateway tools and auth", len(GATEWAY_TOOL_NAMES))
            return mcp

        # Otherwise use cached server
        if self._fastmcp is None:
            assert _FastMCP is not None
            self._fastmcp = _FastMCP(
                self._name,
                instructions=GATEWAY_INSTRUCTIONS,
                website_url=BIFROST_WEBSITE_URL,
                icons=icons,
            )
            register_gateway_tools(self._fastmcp, get_context_fn)
            logger.info("Created FastMCP server with %s gateway tools", len(GATEWAY_TOOL_NAMES))
        return self._fastmcp

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
            # Gemini rejects array schemas without `items`; OpenAI/Anthropic
            # accept the field, so emit it unconditionally.
            array_schema: dict[str, Any] = {"type": "array"}
            if origin is list:
                args = get_args(annotation)
                if args:
                    array_schema["items"] = python_type_to_json_schema(args[0])
                else:
                    array_schema["items"] = {"type": "string"}
            else:
                array_schema["items"] = {"type": "string"}
            return array_schema

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

                if tool_id == "search_knowledge":
                    from src.services.knowledge.search_budget import (
                        MAX_KNOWLEDGE_RESULTS,
                    )

                    parameters["properties"]["limit"].update({
                        "minimum": 1,
                        "maximum": MAX_KNOWLEDGE_RESULTS,
                        "default": MAX_KNOWLEDGE_RESULTS,
                    })

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
        tool_id: The tool ID (e.g., "execute_workflow", "bifrost_agent_list")

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
