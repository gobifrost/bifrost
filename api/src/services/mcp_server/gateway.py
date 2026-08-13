"""Live agent discovery and tool dispatch for the unscoped MCP gateway."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
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
from src.models.orm.executions import Execution
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
MAX_CAPABILITY_RESULTS = 20
MAX_CAPABILITY_RESPONSE_BYTES = 24_000
MAX_EXECUTION_RESULT_BYTES = 24_000

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


def _tool_search_score(tool: ResolvedGatewayTool, query: str) -> int:
    normalized_query = " ".join(_search_tokens(query))
    if not normalized_query:
        return 1

    name = " ".join(_search_tokens(tool.definition.name))
    description = " ".join(_search_tokens(tool.definition.description or ""))
    schema = " ".join(
        _search_tokens(
            str(
                pydantic_core.to_jsonable_python(
                    tool.definition.parameters,
                    fallback=str,
                )
            )
        )
    )
    score = 0
    if normalized_query == name:
        score += 240
    elif normalized_query in name:
        score += 120
    if normalized_query in description:
        score += 50
    if normalized_query in schema:
        score += 15
    for token in set(normalized_query.split()):
        if token in name.split():
            score += 25
        elif token in name:
            score += 15
        if token in description:
            score += 6
        if token in schema:
            score += 2
    return score


def _compact_description(value: str | None, limit: int = 800) -> str | None:
    if value is None or len(value) <= limit:
        return value
    return f"{value[: limit - 20]}… [description cut]"


def _serialized_size(value: Any) -> int:
    return len(pydantic_core.to_json(value, fallback=str))


def _search_again_guidance(
    agent_id: str,
    *,
    complete: bool,
    query: str | None,
) -> str | None:
    if complete:
        return None
    qualifier = (
        "a different or narrower query"
        if query and query.strip()
        else "a query describing the missing capability"
    )
    return (
        "This is not the agent's full tool catalog. Call "
        "bifrost_search_capabilities again with "
        f"agent_id='{agent_id}' and {qualifier}."
    )


def _append_json_pointer(path: str, part: str | int) -> str:
    encoded = str(part).replace("~", "~0").replace("/", "~1")
    return f"{path}/{encoded}" if path else f"/{encoded}"


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

    async def _list_accessible_agents(self) -> list[Agent]:
        from src.core.database import get_db_context

        async with get_db_context() as db:
            repo = self._agent_repo(db)
            if self.context.is_platform_admin:
                return await repo.list_all_in_scope(
                    OrgFilterType.ALL,
                    active_only=True,
                )
            return await repo.list_agents(active_only=True)

    async def accessible_agent_count(self) -> int:
        """Return the caller's live accessible-agent count."""
        return len(await self._list_accessible_agents())

    async def search_capabilities(
        self,
        *,
        query: str | None = None,
        agent_id: str | None = None,
        tool_ref: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search agents and tools, then progressively hydrate one selection."""
        bounded_limit = min(max(limit, 1), MAX_CAPABILITY_RESULTS)
        if agent_id is not None:
            snapshot = await self.get_agent_snapshot(agent_id)
            return self._search_agent_snapshot(
                snapshot,
                query=query,
                tool_ref=tool_ref,
                limit=bounded_limit,
            )

        normalized_query = (query or "").strip()
        if not normalized_query:
            raise GatewayError(
                "INVALID_CAPABILITY_SEARCH",
                "query is required unless agent_id is provided.",
                retryable=True,
            )

        snapshots: list[AgentToolSnapshot] = []
        for agent in await self._list_accessible_agents():
            snapshots.append(await self.get_agent_snapshot(str(agent.id)))

        candidates: list[tuple[int, str, AgentToolSnapshot, ResolvedGatewayTool | None]] = []
        matching_tool_counts: dict[str, int] = {}
        for snapshot in snapshots:
            snapshot_id = str(snapshot.agent.id)
            agent_score = _agent_search_score(snapshot.agent, normalized_query)
            if agent_score > 0:
                candidates.append((agent_score, "agent", snapshot, None))

            matching_tools = 0
            for tool in snapshot.tools:
                score = _tool_search_score(tool, normalized_query)
                if score > 0:
                    matching_tools += 1
                    candidates.append((score, "tool", snapshot, tool))
            matching_tool_counts[snapshot_id] = matching_tools

        candidates.sort(
            key=lambda item: (
                -item[0],
                item[1],
                item[2].agent.name.lower(),
                item[3].definition.name.lower() if item[3] else "",
            )
        )
        selected = candidates[:bounded_limit]
        grouped: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for _score, kind, snapshot, tool in selected:
            snapshot_id = str(snapshot.agent.id)
            if snapshot_id not in grouped:
                order.append(snapshot_id)
                grouped[snapshot_id] = {
                    "snapshot": snapshot,
                    "tools": [],
                    "selected_matches": 0,
                }
            grouped[snapshot_id]["selected_matches"] += 1
            if kind == "tool" and tool is not None:
                grouped[snapshot_id]["tools"].append(tool)

        agents: list[dict[str, Any]] = []
        returned_matches = 0
        response_budget_exhausted = False
        for snapshot_id in order:
            group = grouped[snapshot_id]
            snapshot = group["snapshot"]
            tools = group["tools"]
            agent_result = self._capability_agent_result(
                snapshot,
                tools=tools,
                query=normalized_query,
                include_instructions=False,
                total_matching_tools=matching_tool_counts[snapshot_id],
            )
            proposed = [*agents, agent_result]
            proposed_returned = returned_matches + group["selected_matches"]
            proposed_has_more = len(candidates) > proposed_returned
            budget_probe = {
                "query": query,
                "agent_id": None,
                "tool_ref": None,
                "agents": proposed,
                "returned_matches": proposed_returned,
                "total_matches": len(candidates),
                "has_more_matches": proposed_has_more,
                "response_complete": not proposed_has_more,
                "guidance": self._capability_guidance(),
            }
            if _serialized_size(budget_probe) > MAX_CAPABILITY_RESPONSE_BYTES:
                response_budget_exhausted = True
                break
            agents.append(agent_result)
            returned_matches = proposed_returned

        has_more = len(candidates) > returned_matches or response_budget_exhausted
        return {
            "query": query,
            "agent_id": None,
            "tool_ref": None,
            "agents": agents,
            "returned_matches": returned_matches,
            "total_matches": len(candidates),
            "has_more_matches": has_more,
            "response_complete": not has_more,
            "guidance": self._capability_guidance(),
        }

    def _search_agent_snapshot(
        self,
        snapshot: AgentToolSnapshot,
        *,
        query: str | None,
        tool_ref: str | None,
        limit: int,
    ) -> dict[str, Any]:
        if tool_ref is not None:
            tool = self.find_tool(snapshot, tool_ref)
            tool_result = tool.compact()
            tool_result.update(
                {
                    "input_schema": tool.definition.parameters,
                    "schema_included": True,
                }
            )
            tools = [tool_result]
            total_matches = 1
            returned_matches = 1
            total_matching_tools = 1
        elif query and query.strip():
            scored = [
                (_tool_search_score(tool, query), tool)
                for tool in snapshot.tools
            ]
            scored = [item for item in scored if item[0] > 0]
            scored.sort(
                key=lambda item: (
                    -item[0],
                    item[1].definition.name.lower(),
                    item[1].tool_ref,
                )
            )
            total_matches = len(scored)
            selected = [tool for _, tool in scored[:limit]]
            tools = [self._compact_tool(tool) for tool in selected]
            returned_matches = len(tools)
            total_matching_tools = len(scored)
        else:
            tools = []
            total_matches = 0
            returned_matches = 0
            total_matching_tools = 0

        if tool_ref is not None:
            agent_result = self._capability_agent_result_from_dicts(
                snapshot,
                tools=tools,
                query=query,
                include_instructions=True,
                total_matching_tools=total_matching_tools,
            )
        else:
            agent_result = self._capability_agent_result(
                snapshot,
                tools=[
                    self.find_tool(snapshot, tool["tool_ref"])
                    for tool in tools
                ],
                query=query,
                include_instructions=True,
                total_matching_tools=total_matching_tools,
            )
        has_more = total_matches > returned_matches
        return {
            "query": query,
            "agent_id": str(snapshot.agent.id),
            "tool_ref": tool_ref,
            "agents": [agent_result],
            "returned_matches": returned_matches,
            "total_matches": total_matches,
            "has_more_matches": has_more,
            "response_complete": not has_more,
            "guidance": self._capability_guidance(),
        }

    @staticmethod
    def _compact_tool(tool: ResolvedGatewayTool) -> dict[str, Any]:
        return {
            "tool_ref": tool.tool_ref,
            "name": tool.definition.name,
            "description": _compact_description(tool.definition.description) or "",
            "source": tool.source,
            "input_schema": None,
            "schema_included": False,
        }

    def _capability_agent_result(
        self,
        snapshot: AgentToolSnapshot,
        *,
        tools: list[ResolvedGatewayTool],
        query: str | None,
        include_instructions: bool,
        total_matching_tools: int,
    ) -> dict[str, Any]:
        return self._capability_agent_result_from_dicts(
            snapshot,
            tools=[self._compact_tool(tool) for tool in tools],
            query=query,
            include_instructions=include_instructions,
            total_matching_tools=total_matching_tools,
        )

    @staticmethod
    def _capability_agent_result_from_dicts(
        snapshot: AgentToolSnapshot,
        *,
        tools: list[dict[str, Any]],
        query: str | None,
        include_instructions: bool,
        total_matching_tools: int,
    ) -> dict[str, Any]:
        returned_tools = len(tools)
        total_tools = len(snapshot.tools)
        complete = returned_tools == total_tools
        agent_id = str(snapshot.agent.id)
        return {
            "id": agent_id,
            "name": snapshot.agent.name,
            "description": _compact_description(snapshot.agent.description),
            "instructions": snapshot.agent.system_prompt if include_instructions else None,
            "instructions_included": include_instructions,
            "matching_tools": tools,
            "total_tools": total_tools,
            "returned_tools": returned_tools,
            "complete": complete,
            "total_matching_tools": total_matching_tools,
            "has_more_matches": total_matching_tools > returned_tools,
            "search_again": _search_again_guidance(
                agent_id,
                complete=complete,
                query=query,
            ),
        }

    @staticmethod
    def _capability_guidance() -> str:
        return (
            "matching_tools is always a bounded, query-specific subset, not an "
            "implicit full catalog. Select an agent by calling this tool again "
            "with agent_id to load its instructions. Search again with that "
            "agent_id and a different or narrower query whenever complete is "
            "false. Add tool_ref to load one exact live input schema."
        )

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

    async def execute_agent_tool(
        self,
        agent_id: str,
        tool_ref: str,
        arguments: dict[str, Any],
        *,
        async_execution: bool = False,
    ) -> dict[str, Any]:
        """Re-resolve, validate, and execute an agent-bound tool."""
        snapshot = await self.get_agent_snapshot(agent_id)
        tool = self.find_tool(snapshot, tool_ref)
        return await self.execute_tool(
            snapshot,
            tool,
            arguments,
            async_execution=async_execution,
        )

    async def get_execution(
        self,
        execution_id: str,
        *,
        result_path: str = "",
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return compact status for an execution owned by the caller."""
        from src.core.database import get_db_context
        from src.core.redis_client import get_redis_client

        try:
            parsed_execution_id = UUID(execution_id)
        except (TypeError, ValueError) as exc:
            raise GatewayError(
                "EXECUTION_NOT_FOUND_OR_FORBIDDEN",
                "Execution not found or you do not have access.",
            ) from exc

        async with get_db_context() as db:
            result = await db.execute(
                select(Execution).where(Execution.id == parsed_execution_id)
            )
            execution = result.scalar_one_or_none()

        if execution is not None:
            if (
                not self.context.is_platform_admin
                and execution.executed_by != UUID(str(self.context.user_id))
            ):
                raise GatewayError(
                    "EXECUTION_NOT_FOUND_OR_FORBIDDEN",
                    "Execution not found or you do not have access.",
                )
            result_available = execution.result is not None
            result_value = None
            result_page = None
            if result_available:
                result_value, result_page = self._page_execution_result(
                    execution.result,
                    result_path=result_path,
                    offset=offset,
                    limit=limit,
                )
            return {
                "execution_id": str(execution.id),
                "workflow_id": (
                    str(execution.workflow_id) if execution.workflow_id else None
                ),
                "workflow_name": execution.workflow_name,
                "status": execution.status.value,
                "created_at": execution.created_at,
                "started_at": execution.started_at,
                "completed_at": execution.completed_at,
                "duration_ms": execution.duration_ms,
                "error": execution.error_message,
                "result_available": result_available,
                "result": result_value,
                "result_page": result_page,
            }

        pending = await get_redis_client().get_pending_execution(execution_id)
        if pending is None or (
            not self.context.is_platform_admin
            and pending.get("user_id") != str(self.context.user_id)
        ):
            raise GatewayError(
                "EXECUTION_NOT_FOUND_OR_FORBIDDEN",
                "Execution not found or you do not have access.",
            )
        created_at = pending.get("created_at")
        return {
            "execution_id": execution_id,
            "workflow_id": pending.get("workflow_id"),
            "workflow_name": pending.get("script_name"),
            "status": "Pending",
            "created_at": (
                datetime.fromisoformat(created_at) if created_at else None
            ),
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "error": None,
            "result_available": False,
            "result": None,
            "result_page": None,
        }

    @classmethod
    def _page_execution_result(
        cls,
        value: Any,
        *,
        result_path: str,
        offset: int,
        limit: int,
    ) -> tuple[Any, dict[str, Any]]:
        selected = cls._resolve_result_path(value, result_path)
        bounded_offset = max(offset, 0)
        bounded_limit = min(max(limit, 1), 100)

        if isinstance(selected, dict):
            items = list(selected.items())
            page: dict[str, Any] = {}
            consumed = 0
            for key, item_value in items[
                bounded_offset : bounded_offset + bounded_limit
            ]:
                child_path = _append_json_pointer(result_path, key)
                candidate_value = cls._bounded_result_value(item_value, child_path)
                candidate = {**page, key: candidate_value}
                if _serialized_size(candidate) > MAX_EXECUTION_RESULT_BYTES:
                    break
                page[key] = candidate_value
                consumed += 1
            next_offset = bounded_offset + consumed
            has_more = next_offset < len(items)
            return page, {
                "path": result_path,
                "kind": "object",
                "offset": bounded_offset,
                "returned": consumed,
                "total": len(items),
                "has_more": has_more,
                "next_offset": next_offset if has_more else None,
            }

        if isinstance(selected, list):
            page_list: list[Any] = []
            consumed = 0
            for index, item_value in enumerate(
                selected[bounded_offset : bounded_offset + bounded_limit],
                start=bounded_offset,
            ):
                child_path = _append_json_pointer(result_path, index)
                candidate_value = cls._bounded_result_value(item_value, child_path)
                candidate = [*page_list, candidate_value]
                if _serialized_size(candidate) > MAX_EXECUTION_RESULT_BYTES:
                    break
                page_list.append(candidate_value)
                consumed += 1
            next_offset = bounded_offset + consumed
            has_more = next_offset < len(selected)
            return page_list, {
                "path": result_path,
                "kind": "array",
                "offset": bounded_offset,
                "returned": consumed,
                "total": len(selected),
                "has_more": has_more,
                "next_offset": next_offset if has_more else None,
            }

        if isinstance(selected, str):
            char_limit = min(bounded_limit * 200, MAX_EXECUTION_RESULT_BYTES - 500)
            page_text = selected[bounded_offset : bounded_offset + char_limit]
            next_offset = bounded_offset + len(page_text)
            has_more = next_offset < len(selected)
            return page_text, {
                "path": result_path,
                "kind": "string",
                "offset": bounded_offset,
                "returned": len(page_text),
                "total": len(selected),
                "has_more": has_more,
                "next_offset": next_offset if has_more else None,
                "unit": "characters",
            }

        return selected, {
            "path": result_path,
            "kind": "scalar",
            "offset": 0,
            "returned": 1,
            "total": 1,
            "has_more": False,
            "next_offset": None,
        }

    @staticmethod
    def _bounded_result_value(value: Any, path: str) -> Any:
        if _serialized_size(value) <= MAX_EXECUTION_RESULT_BYTES // 2:
            return value
        return {
            "$omitted": True,
            "path": path,
            "kind": (
                "object"
                if isinstance(value, dict)
                else "array"
                if isinstance(value, list)
                else "string"
                if isinstance(value, str)
                else "scalar"
            ),
            "message": (
                "Value omitted to keep this response bounded. Call "
                "bifrost_get_execution again with this result_path."
            ),
        }

    @staticmethod
    def _resolve_result_path(value: Any, result_path: str) -> Any:
        if not result_path:
            return value
        if not result_path.startswith("/"):
            raise GatewayError(
                "INVALID_RESULT_PATH",
                "result_path must be an RFC 6901 JSON Pointer.",
                retryable=True,
            )
        current = value
        for raw_part in result_path[1:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            try:
                if isinstance(current, dict):
                    current = current[part]
                elif isinstance(current, list):
                    current = current[int(part)]
                else:
                    raise KeyError(part)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise GatewayError(
                    "INVALID_RESULT_PATH",
                    "result_path does not exist in this execution result.",
                    retryable=True,
                    details={"result_path": result_path},
                ) from exc
        return current

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
        *,
        async_execution: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()

        try:
            self.validate_arguments(tool, arguments)
            if async_execution and tool.source != "workflow":
                raise GatewayError(
                    "ASYNC_NOT_SUPPORTED",
                    "Async execution is currently supported only for workflow tools.",
                    retryable=True,
                )
            result = await self._dispatch(
                snapshot.agent,
                tool,
                arguments,
                async_execution=async_execution,
            )
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
        response = {
            "agent_id": str(snapshot.agent.id),
            "agent_name": snapshot.agent.name,
            "tool_ref": tool.tool_ref,
            "tool_name": tool.definition.name,
            "source": tool.source,
            "duration_ms": duration_ms,
            "async": async_execution,
            "execution_id": None,
            "status": None,
            "result": result,
        }
        if async_execution:
            response["execution_id"] = result["execution_id"]
            response["status"] = result["status"]
            response["result"] = None
        return response

    async def _dispatch(
        self,
        agent: Agent,
        tool: ResolvedGatewayTool,
        arguments: dict[str, Any],
        *,
        async_execution: bool = False,
    ) -> Any:
        if tool.source in {"system", "knowledge"}:
            return await self._dispatch_system_tool(agent, tool, arguments)
        if tool.source == "workflow":
            return await self._dispatch_workflow(
                tool,
                arguments,
                async_execution=async_execution,
            )
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
        if structured is not None:
            return structured
        return pydantic_core.to_jsonable_python(
            getattr(result, "content", result),
            fallback=str,
        )

    async def _dispatch_workflow(
        self,
        tool: ResolvedGatewayTool,
        arguments: dict[str, Any],
        *,
        async_execution: bool = False,
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
            sync=not async_execution,
        )
        data = {
            "execution_id": response.execution_id,
            "status": response.status.value,
            "duration_ms": response.duration_ms,
            "result": response.result,
            "error": response.error,
            "error_type": response.error_type,
        }
        if async_execution:
            return {
                "execution_id": response.execution_id,
                "status": response.status.value,
            }
        if response.status.value != "Success":
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                response.error or "Workflow execution failed.",
                details={"underlying_result": data},
            )
        return response.result

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
                result = await mcp_dispatch.invoke(
                    connection=connection,
                    tool_name=tool.remote_tool_name,
                    arguments=arguments,
                    caller_user_id=UUID(str(self.context.user_id)),
                    db=db,
                )
                return self.unwrap_external_result(result)
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

    @staticmethod
    def unwrap_external_result(result: dict[str, Any]) -> Any:
        """Return an external MCP tool's payload without its transport envelope."""
        if result.get("is_error"):
            structured = result.get("structured_content")
            message = (
                structured.get("error")
                if isinstance(structured, dict)
                else None
            )
            raise GatewayError(
                "TOOL_EXECUTION_FAILED",
                str(message or "External MCP tool reported an error."),
                retryable=True,
                details={"underlying_result": result},
            )
        structured = result.get("structured_content")
        if structured is not None:
            return structured
        return result.get("content")
