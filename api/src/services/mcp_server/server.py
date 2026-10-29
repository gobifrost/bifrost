"""
Bifrost MCP Server

MCP server for Bifrost platform capabilities using the bundled MCP SDK.

Architecture:
    - MCPContext: Holds user/org context for permission-scoped tool execution
    - BifrostMCPServer: Creates MCP servers with registered tools
    - Uses the MCP SDK FastMCP transport (Claude Desktop, etc.)

Usage:
    # For external access (MCP SDK HTTP)
    server = BifrostMCPServer(context)
    fastmcp_server = server.get_fastmcp_server()
    app = fastmcp_server.streamable_http_app()
"""

import json
import logging
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools.base import Tool as SDKTool
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp.types import CallToolResult, ContentBlock, Icon


if TYPE_CHECKING:
    from src.core.principal import UserPrincipal

from src.services.mcp_server.agent_scope import (
    get_scoped_agent_id as _get_agent_id_from_scope,
)
from src.services.mcp_server.tools import (
    TOOL_MODULES,
    register_all_tools,
    register_gateway_tools,
)
from src.services.mcp_server.tools.gateway import (
    GATEWAY_INSTRUCTIONS,
    GATEWAY_TOOL_NAMES,
)
from src.services.mcp_server.tool_result import ToolResult, error_result, success_result
from src.services.tool_schema import parameters_json_schema

logger = logging.getLogger(__name__)

# Bifrost branding
BIFROST_ICON_URL = "https://bifrostintegrations.blob.core.windows.net/public/logo.svg"
BIFROST_WEBSITE_URL = "https://docs.gobifrost.com"


# =============================================================================
# Workflow Tool Name Mapping
# =============================================================================

# Workflow tools are registered with normalized names for MCP compatibility.
# The reverse mapping lets agent-scoped access resolve a workflow UUID to its
# current FastMCP tool name. Its values also track names during refresh.
_WORKFLOW_ID_TO_TOOL_NAME: dict[str, str] = {}

# Stored references for refresh_workflow_tools()
_fastmcp_instance: FastMCP | None = None

# Dynamic workflow schemas are validated by ``SDKWorkflowTool.run``. Every
# wrapper therefore shares this empty SDK argument model instead of creating a
# synthetic Pydantic model from its ``**arguments`` signature.
_WORKFLOW_TOOL_FN_METADATA = FuncMetadata(arg_model=ArgModelBase)


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
    Get MCPContext from the authenticated MCP SDK token.

    This extracts user information from the validated JWT token set by
    the SDK authentication middleware. Used by tool execution to get
    the actual authenticated user instead of the default startup context.

    Returns:
        MCPContext populated with authenticated user's information

    Raises:
        ToolError: If no authenticated user (token missing or invalid)
    """
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

    Populates ``accessible_namespaces`` only for the agent-scoped mount:

    - ``/mcp/{agent_id}`` (agent-scoped): namespaces == that agent's
      ``knowledge_sources`` only. Cross-namespace requests are rejected
      by the tool itself, so a session bound to an agent can only see
      that agent's knowledge.
    - ``/mcp`` (unscoped): the gateway resolves the selected agent and
      constructs a tool-specific context at dispatch time.
    """
    from src.core.database import get_db_context
    from src.services.mcp_server.tool_access import MCPToolAccessService
    from src.services.mcp_server.authorization import load_mcp_caller

    token = get_access_token()
    if token is None:
        raise ToolError("Authentication required")

    is_superuser = token.claims.get("is_superuser", False)
    is_provider_org = token.claims.get("is_provider_org", False)
    is_external = token.claims.get("is_external", False)
    agent_id = _get_agent_id_from_scope()

    accessible_namespaces: list[str] = []
    if agent_id is not None:
        try:
            async with get_db_context() as db:
                service = MCPToolAccessService(db)
                agent_result = await service.get_tools_for_agent(
                    agent_id=agent_id,
                    caller=await load_mcp_caller(db, _get_context_from_token()),
                    for_execution=True,
                )
                if agent_result is not None:
                    accessible_namespaces = list(agent_result.accessible_namespaces)
        except Exception as e:
            logger.warning(f"Failed to resolve accessible namespaces: {e}")

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
        accessible_namespaces=accessible_namespaces,
    )


# =============================================================================
# BifrostMCPServer
# =============================================================================


class BifrostSDKServer(FastMCP):
    """MCP SDK server with Bifrost's existing fail-closed tool policy."""

    async def list_tools(self) -> list:
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        return await ToolFilterMiddleware().filter_tools(await super().list_tools())

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        await ToolFilterMiddleware().authorize_tool_call(name)
        return await super().call_tool(name, arguments)


class BifrostMCPServer:
    """
    Bifrost MCP Server using the bundled MCP SDK.

    Creates MCP servers with tools registered based on user context and
    permissions. Uses the MCP SDK for HTTP access (Claude Desktop, etc.).

    Usage:
        # Create server with context
        context = MCPContext(user_id=user.id, org_id=user.org_id)
        server = BifrostMCPServer(context)

        # For MCP SDK HTTP use
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

        # MCP SDK server (lazy initialized)
        self._fastmcp: Any = None

    def get_fastmcp_server(self, auth: Any = None) -> FastMCP:
        """
        Get the MCP SDK server for HTTP access.

        The server is cached for reuse. If auth is provided, a new server
        is created with authentication enabled.

        Args:
            auth: Optional authentication provider (e.g., token verifier).
                  If provided, creates a new server with auth.

        Returns:
            MCP SDK server instance
        """
        # Build icon list for branding
        icons = [
            Icon(
                src=BIFROST_ICON_URL,
                mimeType="image/svg+xml",
                sizes=["any"],
            )
        ]

        # Per-request context: pull user from the JWT and scope namespaces for
        # the agent-specific mount. Falls back to the server's
        # default_context only when there is no MCP request context at all
        # (e.g. tool introspection at startup); other failures (auth required,
        # DB error) are real errors and must not silently degrade to the
        # default admin context.
        default_context = self.context

        async def get_context_fn() -> MCPContext:
            try:
                return await _get_runtime_context()
            except ToolError:
                # ToolError is raised when no auth token exists in the
                # current call — for HTTP requests that means unauthenticated
                # and should bubble up; for tool introspection at startup
                # there is no HTTP context, so fall back to default.
                return default_context

        # The ASGI adapter owns authentication; use a fresh server for its
        # isolated application lifecycle when an adapter is requested.
        if auth is not None:
            mcp = BifrostSDKServer(
                self._name,
                instructions=GATEWAY_INSTRUCTIONS,
                website_url=BIFROST_WEBSITE_URL,
                icons=icons,
            )
            register_gateway_tools(mcp, get_context_fn)
            register_all_tools(mcp, get_context_fn)
            tool_count = (
                len(GATEWAY_TOOL_NAMES)
                + sum(len(m.TOOLS) for m in TOOL_MODULES)
            )
            logger.info(f"Created MCP SDK server with {tool_count} tools and auth")
            return mcp

        # Otherwise use cached server
        if self._fastmcp is None:
            self._fastmcp = BifrostSDKServer(
                self._name,
                instructions=GATEWAY_INSTRUCTIONS,
                website_url=BIFROST_WEBSITE_URL,
                icons=icons,
            )
            register_gateway_tools(self._fastmcp, get_context_fn)
            register_all_tools(self._fastmcp, get_context_fn)
            tool_count = (
                len(GATEWAY_TOOL_NAMES)
                + sum(len(m.TOOLS) for m in TOOL_MODULES)
            )
            logger.info(f"Created MCP SDK server with {tool_count} tools")
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


# =============================================================================
# Workflow Tools (Dynamic from Database)
# =============================================================================

# Workflow tool adapter for dynamically registered workflows.
class WorkflowTool:
        """
        Adapter that executes a Bifrost workflow.

        Its schema is installed on the bundled SDK registry after wrapping
        ``run`` in a callable, because workflows define their argument shape
        dynamically in the database.

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

        def __init__(
            self,
            *,
            name: str,
            description: str,
            workflow_id: str,
            workflow_name: str,
            parameters: dict[str, Any],
        ) -> None:
            self.name = name
            self.description = description
            self.workflow_id = workflow_id
            self.workflow_name = workflow_name
            self.parameters = parameters

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

                from pydantic import BaseModel

                if isinstance(result, BaseModel):
                    result = result.model_dump(mode="json")

                # Auto-wrap dict results
                if isinstance(result, dict):
                    if result.get("error"):
                        return error_result(str(result["error"]), result)
                    from src.services.chat_artifacts import find_artifact_refs

                    artifact_refs = find_artifact_refs(result)
                    if artifact_refs:
                        import base64

                        from mcp.types import ImageContent, ResourceLink, TextContent

                        from src.services.mcp_server.tools._http_bridge import rest_client

                        content_blocks: list[Any] = [
                            TextContent(
                                type="text",
                                text=f"Created {len(artifact_refs)} artifact(s).",
                            )
                        ]
                        async with rest_client(context) as client:
                            for ref in artifact_refs:
                                endpoint = "content" if ref.content_type.startswith("image/") else "download-url"
                                response = await client.get(f"/api/sdk/artifacts/{ref.id}/{endpoint}")
                                if response.status_code != 200:
                                    return error_result(
                                        "ArtifactRef output was not found or is outside this MCP scope.",
                                        result,
                                    )
                                if ref.content_type.startswith("image/"):
                                    content_blocks.append(
                                        ImageContent(
                                            type="image",
                                            data=base64.b64encode(response.content).decode("ascii"),
                                            mimeType=ref.content_type,
                                        )
                                    )
                                else:
                                    content_blocks.append(
                                        ResourceLink(
                                            type="resource_link",
                                            name=ref.filename,
                                            uri=response.json()["url"],
                                            mimeType=ref.content_type,
                                            size=ref.size_bytes,
                                        )
                                    )
                        return ToolResult(
                            content=content_blocks,
                            structured_content=result,
                        )
                    # Format as pretty JSON for display
                    display = json.dumps(result, indent=2, default=str)
                    return success_result(display, result)

                # Fallback for other types
                display = str(result)
                return success_result(display, {"result": result})

            except Exception as e:
                logger.exception(f"Error executing workflow tool {self.workflow_name}: {e}")
                return error_result(str(e))


class SDKWorkflowTool(SDKTool):
    """SDK tool that validates and dispatches a database-defined workflow."""

    async def run(
        self,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> CallToolResult:
        """Validate the registered schema instead of synthetic ``**kwargs`` metadata."""
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import ValidationError

        try:
            Draft202012Validator(self.parameters).validate(arguments)
        except ValidationError as exc:
            raise ToolError(f"Invalid arguments for {self.name}: {exc.message}") from exc
        return await self.fn(**arguments)

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
    """Launch a currently bound tool through its parent Agent's shared gate."""
    from fastapi import HTTPException
    from src.core.database import get_db_context
    from src.services.execution.agent_workflow_tools import (
        execute_agent_workflow_tool,
    )
    from src.services.authorization.callers import CallerUnavailable
    from src.services.mcp_server.authorization import load_mcp_caller

    agent_id = _get_agent_id_from_scope()
    if agent_id is None:
        return {"error": "Workflow tools require an Agent-scoped MCP session"}
    try:
        async with get_db_context() as db:
            caller = await load_mcp_caller(db, context)
        response = await execute_agent_workflow_tool(
            agent_id=agent_id, workflow_id=workflow_id, parameters=inputs,
            caller=caller, sync=True,
        )
    except HTTPException as exc:
        return {"error": exc.detail}
    except CallerUnavailable as exc:
        return {"error": str(exc)}
    if response.status.value == "Success":
        return response.result
    return {"error": response.error or f"Workflow '{workflow_name}' failed"}


async def _notify_duplicate_workflow_names(duplicates: dict[str, list]) -> None:
    """Log warning about duplicate workflow names."""
    for name, workflows in duplicates.items():
        workflow_names = [w.name for w in workflows]
        logger.warning(
            f"Multiple workflows normalize to '{name}': {workflow_names}. "
            "Consider renaming to avoid confusion."
        )


async def _register_workflow_tools(mcp: FastMCP) -> int:
    """
    Register workflow tools with the MCP SDK using human-readable names.

    Installs each workflow's database-defined JSON schema on its SDK tool.

    Tool names are normalized from workflow names (e.g., "Review Tickets" -> "review_tickets").
    Duplicate names get a short random suffix (e.g., "review_tickets_x7k").

    Returns:
        Number of workflow tools registered
    """
    global _WORKFLOW_ID_TO_TOOL_NAME, _fastmcp_instance

    # Store the live server so refresh_workflow_tools() can re-register tools.
    _fastmcp_instance = mcp

    from src.core.database import get_db_context
    from src.services.tool_registry import ToolRegistry

    try:
        async with get_db_context() as db:
            registry = ToolRegistry(db)
            tools = await registry.get_all_tools()

            # Clear previous mappings (in case of re-registration)
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
            native_tool_names = {
                tool_id for m in TOOL_MODULES for tool_id, _, _ in m.TOOLS
            } | set(GATEWAY_TOOL_NAMES)

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

                    # Store the registered name for agent-scoped lookups.
                    _WORKFLOW_ID_TO_TOOL_NAME[workflow_id] = tool_name

                    # Create WorkflowTool with human-readable name
                    # Context is retrieved dynamically from authenticated token at runtime
                    workflow_tool = WorkflowTool(
                        name=tool_name,  # Human-readable name instead of UUID
                        description=description,
                        workflow_id=workflow_id,
                        workflow_name=workflow_name,
                        parameters=parameters_json_schema(
                            tool.parameters_schema,
                            allow_unknown_when_empty=True,
                        ),
                    )

                    # Install the dynamic workflow schema used by tools/list
                    # and SDKWorkflowTool's validator.
                    try:
                        def build_workflow_wrapper(bound_tool: WorkflowTool):
                            async def workflow_wrapper(
                                **arguments: Any,
                            ) -> CallToolResult:
                                return (
                                    await bound_tool.run(arguments)
                                ).to_call_tool_result()

                            return workflow_wrapper

                        workflow_wrapper = build_workflow_wrapper(workflow_tool)

                        workflow_wrapper.__name__ = tool_name
                        workflow_wrapper.__qualname__ = tool_name
                        workflow_wrapper.__doc__ = description
                        registered = SDKWorkflowTool(
                            fn=workflow_wrapper,
                            name=tool_name,
                            description=description,
                            parameters=workflow_tool.parameters,
                            fn_metadata=_WORKFLOW_TOOL_FN_METADATA,
                            is_async=True,
                        )
                        # SDK add_tool preserves an existing name. Refresh must
                        # replace its callable as well as its dynamic schema.
                        mcp._tool_manager._tools[tool_name] = registered
                        count += 1
                        logger.debug(
                            f"Registered workflow tool: {tool_name} "
                            f"(workflow: {workflow_name}, id: {workflow_id})"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to register workflow tool {workflow_name}: {e}")

            logger.info(f"Registered {count} workflow tools with MCP SDK")
            return count

    except Exception as e:
        logger.exception(f"Error registering workflow tools: {e}")
        return 0


async def refresh_workflow_tools() -> int:
    """Re-register all workflow tools from DB. Removes stale tools.

    Call this after workflow create/update/delete to keep MCP tool list current.
    """
    if not _fastmcp_instance:
        logger.debug("MCP not initialized, skipping workflow tool refresh")
        return 0

    old_tool_names = set(_WORKFLOW_ID_TO_TOOL_NAME.values())
    count = await _register_workflow_tools(_fastmcp_instance)
    new_tool_names = set(_WORKFLOW_ID_TO_TOOL_NAME.values())

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
