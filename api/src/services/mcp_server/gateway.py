"""Live agent discovery and tool dispatch for the unscoped MCP gateway."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid5

import pydantic_core
from jsonschema import FormatChecker
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.org_filter import OrgFilterType
from src.models.orm.agents import Agent
from src.models.orm.external_mcp import MCPConnection, MCPServer
from src.repositories.agents import AgentRepository
from src.services.execution.agent_helpers import (
    find_delegated_agent,
    parse_mcp_tool_name,
    resolve_agent_tools,
)
from src.services.llm import ToolDefinition
from src.services.mcp_client import dispatch as mcp_dispatch
from src.services.mcp_client.errors import (
    MisconfigError,
    NeedsReauthError,
    ToolDispatchError,
)
from src.services.mcp_server.config_service import MCPConfig, MCPConfigService

if TYPE_CHECKING:
    from src.services.mcp_server.server import MCPContext

logger = logging.getLogger(__name__)

GATEWAY_TOOL_NAMESPACE = UUID("bcf3f4d7-b95e-53cc-9b7f-35e7081d0d84")
MAX_AGENT_RESULTS = 20

GatewayToolSource = Literal[
    "system",
    "knowledge",
    "workflow",
    "delegation",
    "external_mcp",
]


class GatewayError(Exception):
    """Structured, model-repairable gateway failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            **self.details,
        }


@dataclass(frozen=True)
class ResolvedGatewayTool:
    """One live tool resolved within an agent capability boundary."""

    tool_ref: str
    definition: ToolDefinition
    source: GatewayToolSource
    source_identity: str
    source_id: UUID | None = None
    remote_tool_name: str | None = None

    def compact(self) -> dict[str, Any]:
        return {
            "tool_ref": self.tool_ref,
            "name": self.definition.name,
            "description": self.definition.description,
            "source": self.source,
        }


@dataclass
class AgentToolSnapshot:
    """Accessible agent plus its live, filtered tool catalog."""

    agent: Agent
    tools: list[ResolvedGatewayTool]


def _json_pointer(path: Any) -> str:
    parts = [
        str(part).replace("~", "~0").replace("/", "~1")
        for part in path
    ]
    return "/" + "/".join(parts) if parts else "/"


def _search_tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def _agent_search_score(agent: Agent, query: str) -> int:
    normalized_query = " ".join(_search_tokens(query))
    if not normalized_query:
        return 1

    name = " ".join(_search_tokens(agent.name))
    description = " ".join(_search_tokens(agent.description or ""))
    instructions = " ".join(_search_tokens(agent.system_prompt or ""))
    score = 0

    if normalized_query == name:
        score += 200
    elif normalized_query in name:
        score += 100
    if normalized_query in description:
        score += 40
    if normalized_query in instructions:
        score += 10

    for token in set(normalized_query.split()):
        if token in name.split():
            score += 20
        elif token in name:
            score += 12
        if token in description:
            score += 5
        if token in instructions:
            score += 1

    return score


class MCPAgentGatewayService:
    """Resolve and execute agent capabilities for one authenticated MCP caller."""

    def __init__(self, context: MCPContext):
        self.context = context

    def _agent_repo(self, session: Any) -> AgentRepository:
        return AgentRepository(
            session,
            org_id=self.context.org_id,
            user_id=self.context.user_id,
            is_superuser=self.context.is_platform_admin,
            is_external=self.context.is_external,
        )

    async def find_agents(
        self,
        *,
        query: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        from src.core.database import get_db_context

        bounded_limit = min(max(limit, 1), MAX_AGENT_RESULTS)
        async with get_db_context() as db:
            repo = self._agent_repo(db)
            if self.context.is_platform_admin:
                agents = await repo.list_all_in_scope(
                    OrgFilterType.ALL,
                    active_only=True,
                )
            else:
                agents = await repo.list_agents(active_only=True)

        scored = [
            (_agent_search_score(agent, query or ""), agent)
            for agent in agents
        ]
        if query and query.strip():
            scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (-item[0], item[1].name.lower(), str(item[1].id)))

        matches = scored[:bounded_limit]
        return {
            "query": query,
            "agents": [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "description": agent.description,
                }
                for _, agent in matches
            ],
            "count": len(matches),
            "total_matches": len(scored),
            "has_more": len(scored) > len(matches),
        }

    async def get_agent_snapshot(self, agent_id: str) -> AgentToolSnapshot:
        from src.core.database import get_db_context

        try:
            parsed_agent_id = UUID(agent_id)
        except (TypeError, ValueError) as exc:
            raise GatewayError(
                "AGENT_NOT_FOUND_OR_FORBIDDEN",
                "Agent not found or you do not have access.",
            ) from exc

        async with get_db_context() as db:
            repo = self._agent_repo(db)
            agent = await repo.get_agent_with_access_check(parsed_agent_id)
            if agent is None or not agent.is_active:
                raise GatewayError(
                    "AGENT_NOT_FOUND_OR_FORBIDDEN",
                    "Agent not found or you do not have access.",
                )

            definitions, id_map = await resolve_agent_tools(
                agent,
                db,
                caller_user_id=UUID(str(self.context.user_id)),
            )
            config = await MCPConfigService(db).get_config()
            tools = self._resolve_gateway_tools(agent, definitions, id_map, config)

        return AgentToolSnapshot(agent=agent, tools=tools)

    async def get_agent(self, agent_id: str) -> dict[str, Any]:
        """Return one live agent capability package."""
        snapshot = await self.get_agent_snapshot(agent_id)
        return {
            "agent": {
                "id": str(snapshot.agent.id),
                "name": snapshot.agent.name,
                "description": snapshot.agent.description,
                "instructions": snapshot.agent.system_prompt,
            },
            "tools": [tool.compact() for tool in snapshot.tools],
            "tool_count": len(snapshot.tools),
        }

    async def get_tool_schema(
        self,
        agent_id: str,
        tool_ref: str,
    ) -> dict[str, Any]:
        """Return the exact current schema for an agent-bound tool."""
        snapshot = await self.get_agent_snapshot(agent_id)
        tool = self.find_tool(snapshot, tool_ref)
        return {
            "agent_id": str(snapshot.agent.id),
            "tool_ref": tool.tool_ref,
            "name": tool.definition.name,
            "description": tool.definition.description,
            "source": tool.source,
            "input_schema": tool.definition.parameters,
        }

    async def execute_agent_tool(
        self,
        agent_id: str,
        tool_ref: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Re-resolve, validate, and execute an agent-bound tool."""
        snapshot = await self.get_agent_snapshot(agent_id)
        tool = self.find_tool(snapshot, tool_ref)
        return await self.execute_tool(snapshot, tool, arguments)

    def _resolve_gateway_tools(
        self,
        agent: Agent,
        definitions: list[ToolDefinition],
        id_map: dict[str, UUID],
        config: MCPConfig,
    ) -> list[ResolvedGatewayTool]:
        resolved: list[ResolvedGatewayTool] = []

        for definition in definitions:
            source: GatewayToolSource
            source_id: UUID | None = None
            remote_tool_name: str | None = None

            mcp_route = parse_mcp_tool_name(definition.name)
            if mcp_route is not None:
                source = "external_mcp"
                source_id, remote_tool_name = mcp_route
                source_identity = f"external_mcp:{source_id}:{remote_tool_name}"
            elif definition.name == "search_knowledge":
                source = "knowledge"
                source_identity = f"knowledge:{definition.name}"
            elif definition.name in (agent.system_tools or []):
                source = "system"
                source_identity = f"system:{definition.name}"
            else:
                delegated = find_delegated_agent(agent, definition.name)
                if delegated is not None:
                    source = "delegation"
                    source_id = delegated.id
                    source_identity = f"delegation:{delegated.id}"
                else:
                    workflow_id = id_map.get(definition.name)
                    if workflow_id is None:
                        logger.warning(
                            "Gateway could not classify resolved tool %s for agent %s",
                            definition.name,
                            agent.id,
                        )
                        continue
                    source = "workflow"
                    source_id = workflow_id
                    source_identity = f"workflow:{workflow_id}"

            filter_ids = {definition.name}
            if source_id is not None:
                filter_ids.add(str(source_id))
            if config.allowed_tool_ids and not (
                filter_ids & set(config.allowed_tool_ids)
            ):
                continue
            if config.blocked_tool_ids and (
                filter_ids & set(config.blocked_tool_ids)
            ):
                continue

            tool_ref = str(
                uuid5(
                    GATEWAY_TOOL_NAMESPACE,
                    f"{agent.id}:{source_identity}",
                )
            )
            resolved.append(
                ResolvedGatewayTool(
                    tool_ref=tool_ref,
                    definition=definition,
                    source=source,
                    source_identity=source_identity,
                    source_id=source_id,
                    remote_tool_name=remote_tool_name,
                )
            )

        return resolved

    @staticmethod
    def find_tool(
        snapshot: AgentToolSnapshot,
        tool_ref: str,
    ) -> ResolvedGatewayTool:
        for tool in snapshot.tools:
            if tool.tool_ref == tool_ref:
                return tool
        raise GatewayError(
            "TOOL_NOT_FOUND_OR_FORBIDDEN",
            "Tool not found, no longer granted, or you do not have access.",
            retryable=True,
            details={"agent_id": str(snapshot.agent.id), "tool_ref": tool_ref},
        )

    @staticmethod
    def validate_arguments(
        tool: ResolvedGatewayTool,
        arguments: dict[str, Any],
    ) -> None:
        schema = tool.definition.parameters or {
            "type": "object",
            "properties": {},
        }
        try:
            validator_class = validator_for(schema)
            validator_class.check_schema(schema)
            validator = validator_class(
                schema,
                format_checker=FormatChecker(),
            )
        except SchemaError as exc:
            raise GatewayError(
                "TOOL_SCHEMA_INVALID",
                "The live tool schema is invalid and cannot be executed.",
                details={
                    "tool_ref": tool.tool_ref,
                    "tool_name": tool.definition.name,
                    "schema_error": exc.message,
                },
            ) from exc

        errors = sorted(
            validator.iter_errors(arguments),
            key=lambda error: [str(part) for part in error.absolute_path],
        )
        if not errors:
            return

        issues = [
            {
                "path": _json_pointer(error.absolute_path),
                "message": error.message,
                "validator": error.validator,
                "expected": pydantic_core.to_jsonable_python(
                    error.validator_value,
                    fallback=str,
                ),
            }
            for error in errors[:10]
        ]
        raise GatewayError(
            "INVALID_ARGUMENTS",
            "Arguments do not match the live tool schema.",
            retryable=True,
            details={
                "tool_ref": tool.tool_ref,
                "tool_name": tool.definition.name,
                "issues": issues,
                "input_schema": schema,
            },
        )

    async def execute_tool(
        self,
        snapshot: AgentToolSnapshot,
        tool: ResolvedGatewayTool,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.monotonic()

        try:
            self.validate_arguments(tool, arguments)
            result = await self._dispatch(snapshot.agent, tool, arguments)
        except GatewayError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            exc.details.setdefault("agent_id", str(snapshot.agent.id))
            exc.details.setdefault("tool_ref", tool.tool_ref)
            exc.details.setdefault("tool_name", tool.definition.name)
            exc.details.setdefault("source", tool.source)
            logger.warning(
                "MCP gateway execution rejected: caller=%s agent=%s tool=%s "
                "source=%s code=%s duration_ms=%s",
                self.context.user_id,
                snapshot.agent.id,
                tool.definition.name,
                tool.source,
                exc.code,
                duration_ms,
            )
            raise
        except Exception as exc:
            logger.exception(
                "MCP gateway execution failed: caller=%s agent=%s tool=%s source=%s",
                self.context.user_id,
                snapshot.agent.id,
                tool.definition.name,
                tool.source,
            )
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                str(exc),
                details={
                    "agent_id": str(snapshot.agent.id),
                    "tool_ref": tool.tool_ref,
                    "tool_name": tool.definition.name,
                    "source": tool.source,
                },
            ) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "MCP gateway execution: caller=%s agent=%s tool=%s source=%s duration_ms=%s",
            self.context.user_id,
            snapshot.agent.id,
            tool.definition.name,
            tool.source,
            duration_ms,
        )
        return {
            "agent_id": str(snapshot.agent.id),
            "agent_name": snapshot.agent.name,
            "tool_ref": tool.tool_ref,
            "tool_name": tool.definition.name,
            "source": tool.source,
            "duration_ms": duration_ms,
            "result": result,
        }

    async def _dispatch(
        self,
        agent: Agent,
        tool: ResolvedGatewayTool,
        arguments: dict[str, Any],
    ) -> Any:
        if tool.source in {"system", "knowledge"}:
            return await self._dispatch_system_tool(agent, tool, arguments)
        if tool.source == "workflow":
            return await self._dispatch_workflow(tool, arguments)
        if tool.source == "delegation":
            return await self._dispatch_delegation(agent, tool, arguments)
        if tool.source == "external_mcp":
            return await self._dispatch_external_mcp(tool, arguments)
        raise GatewayError(
            "TOOL_EXECUTION_FAILED",
            f"Unsupported tool source: {tool.source}",
        )

    async def _dispatch_system_tool(
        self,
        agent: Agent,
        tool: ResolvedGatewayTool,
        arguments: dict[str, Any],
    ) -> Any:
        from src.services.mcp_server.server import (
            MCPContext,
            get_system_tool_function,
        )

        func = get_system_tool_function(tool.definition.name)
        if func is None:
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                f"System tool '{tool.definition.name}' is no longer available.",
                retryable=True,
            )

        context = MCPContext(
            user_id=self.context.user_id,
            org_id=self.context.org_id,
            is_platform_admin=self.context.is_platform_admin,
            is_external=self.context.is_external,
            user_email=self.context.user_email,
            user_name=self.context.user_name,
            accessible_namespaces=list(agent.knowledge_sources or []),
        )
        result = await func(context, **arguments)
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict) and structured.get("error"):
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                str(structured["error"]),
                retryable=True,
                details={"underlying_result": structured},
            )
        return {
            "content": pydantic_core.to_jsonable_python(
                getattr(result, "content", result),
                fallback=str,
            ),
            "structured_content": structured,
        }

    async def _dispatch_workflow(
        self,
        tool: ResolvedGatewayTool,
        arguments: dict[str, Any],
    ) -> Any:
        from src.services.execution.service import execute_tool

        if tool.source_id is None:
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                "Workflow identity is missing.",
            )
        response = await execute_tool(
            workflow_id=str(tool.source_id),
            workflow_name=tool.definition.name,
            parameters=arguments,
            user_id=str(self.context.user_id),
            user_email=self.context.user_email,
            user_name=self.context.user_name or "MCP User",
            org_id=str(self.context.org_id) if self.context.org_id else None,
            is_platform_admin=self.context.is_platform_admin,
            is_agent=True,
        )
        data = {
            "execution_id": response.execution_id,
            "status": response.status.value,
            "duration_ms": response.duration_ms,
            "result": response.result,
            "error": response.error,
            "error_type": response.error_type,
        }
        if response.status.value != "Success":
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                response.error or "Workflow execution failed.",
                details={"underlying_result": data},
            )
        return data

    async def _dispatch_delegation(
        self,
        agent: Agent,
        tool: ResolvedGatewayTool,
        arguments: dict[str, Any],
    ) -> Any:
        from src.core.cache import get_shared_redis
        from src.core.database import get_db_context, get_session_factory
        from src.services.execution.autonomous_agent_executor import (
            AutonomousAgentExecutor,
        )

        if tool.source_id is None:
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                "Delegated agent identity is missing.",
            )
        task = arguments.get("task")
        if not isinstance(task, str) or not task.strip():
            raise GatewayError(
                "INVALID_ARGUMENTS",
                "Delegation requires a non-empty task.",
                retryable=True,
            )

        async with get_db_context() as db:
            delegated = await AgentRepository(
                db,
                org_id=agent.organization_id,
                user_id=self.context.user_id,
                is_superuser=True,
                is_external=False,
            ).get_agent(tool.source_id)
        if delegated is None or not delegated.is_active:
            raise GatewayError(
                "TOOL_NOT_FOUND_OR_FORBIDDEN",
                "Delegated agent is no longer available.",
                retryable=True,
            )

        executor = AutonomousAgentExecutor(
            get_session_factory(),
            redis_client=await get_shared_redis(),
        )
        result = await executor.run(
            agent=delegated,
            input_data={"task": task, "_delegated_from": agent.name},
            _caller={"user_id": str(self.context.user_id)},
        )
        if result.get("status") == "failed":
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                str(result.get("error") or "Delegated agent execution failed."),
                details={"underlying_result": result},
            )
        return result

    async def _dispatch_external_mcp(
        self,
        tool: ResolvedGatewayTool,
        arguments: dict[str, Any],
    ) -> Any:
        from src.core.database import get_db_context

        if tool.source_id is None or tool.remote_tool_name is None:
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                "External MCP tool identity is incomplete.",
            )
        try:
            async with get_db_context() as db:
                result = await db.execute(
                    select(MCPConnection)
                    .where(MCPConnection.id == tool.source_id)
                    .options(
                        selectinload(MCPConnection.server).selectinload(
                            MCPServer.oauth_provider
                        ),
                        selectinload(MCPConnection.service_oauth_token),
                    )
                )
                connection = result.scalar_one_or_none()
                if connection is None:
                    raise GatewayError(
                        "TOOL_NOT_FOUND_OR_FORBIDDEN",
                        "External MCP connection is no longer available.",
                        retryable=True,
                    )
                return await mcp_dispatch.invoke(
                    connection=connection,
                    tool_name=tool.remote_tool_name,
                    arguments=arguments,
                    caller_user_id=UUID(str(self.context.user_id)),
                    db=db,
                )
        except NeedsReauthError as exc:
            raise GatewayError(
                "NEEDS_REAUTH",
                str(exc),
                retryable=True,
                details={
                    "reauth_url": exc.reauth_url,
                    "connection_id": str(exc.connection_id),
                    "tool_name": tool.remote_tool_name,
                },
            ) from exc
        except MisconfigError as exc:
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                str(exc),
                details={"connection_id": str(tool.source_id)},
            ) from exc
        except ToolDispatchError as exc:
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                str(exc),
                retryable=True,
                details={"connection_id": str(tool.source_id)},
            ) from exc
