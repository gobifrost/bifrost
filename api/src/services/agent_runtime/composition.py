"""Database-authorized adapter for the pure agent-composition view."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm.agents import Agent, AgentDelegation
from src.services.agent_runtime.composition_view import (
    COMPOSITION_INSTRUCTIONS,
    AgentCompositionSnapshot,
    AgentCompositionView,
    DeferredAgentSnapshot,
    ToolBinding,
    _capability_id,
    _delegate_name,
    _qualified_tool_name,
)
from src.services.agent_runtime.toolset import ToolEventHandler, ToolExecutor
from src.services.authorization.agent_runtime import (
    AgentRuntimeAuthorizationError,
    authorize_agent_execution,
)
from src.services.authorization.enforce import Caller
from src.services.capabilities.catalog import (
    CapabilityArgumentError,
    capability_ref,
    require_valid_capability_arguments,
    resolve_capability_route,
)
from src.services.execution.agent_helpers import agent_delegation_slug, resolve_agent_tools
from src.services.llm import ToolDefinition


class AgentCompositionError(AgentRuntimeAuthorizationError):
    """A requested framework capability or tool is no longer permitted."""


@dataclass(frozen=True)
class ResolvedToolCall:
    """A live, exact owner/tool binding approved for one executor dispatch."""

    owner: Agent
    original_name: str
    arguments: dict[str, Any]
    workflow_ids: dict[str, UUID]
    provenance: dict[str, Any]


@dataclass(frozen=True)
class _DeferredAgent:
    """ORM builder input, converted to a pure snapshot before model execution."""

    agent: Agent
    path: tuple[UUID, ...]
    definitions: tuple[ToolDefinition, ...]
    capability_id: str


async def _load_approved_child(
    session: AsyncSession,
    *,
    parent: Agent,
    child_id: UUID,
) -> Agent | None:
    """Load one currently-active edge using the legacy delegation scope rule."""
    result = await session.execute(
        select(Agent)
        .join(AgentDelegation, AgentDelegation.child_agent_id == Agent.id)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.delegated_agents),
            selectinload(Agent.mcp_connections),
            selectinload(Agent.roles),
        )
        .where(
            Agent.id == child_id,
            Agent.is_active.is_(True),
            AgentDelegation.parent_agent_id == parent.id,
            or_(
                Agent.organization_id.is_(None),
                Agent.organization_id == parent.organization_id,
            ),
        )
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


class AgentComposition:
    """Live authorization adapter around an immutable, model-visible snapshot."""

    def __init__(
        self,
        *,
        root_id: UUID,
        caller: Caller,
        session_factory: Callable[[], Any],
        caller_user_id: UUID | None,
        instructions: str,
        entry_definitions: Sequence[ToolDefinition],
        bindings: dict[str, ToolBinding],
        deferred: Sequence[DeferredAgentSnapshot],
        isolated_delegates: dict[str, ToolBinding],
    ) -> None:
        self.caller = caller
        self.session_factory = session_factory
        self.caller_user_id = caller_user_id
        self._view = AgentCompositionView(
            AgentCompositionSnapshot.from_parts(
                root_id=root_id,
                instructions=instructions,
                entry_definitions=entry_definitions,
                bindings=bindings,
                deferred=deferred,
                isolated_delegates=isolated_delegates,
            ),
            authorize_capability_load=lambda capability_id: self.authorize_capability_load(
                capability_id
            ),
            error_type=AgentCompositionError,
        )

    @property
    def snapshot(self) -> AgentCompositionSnapshot:
        """The serializable model-visible plan, without live authority."""
        return self._view.snapshot

    @property
    def root_id(self) -> UUID:
        return self._view.root_id

    @property
    def instructions(self) -> str:
        return self._view.instructions

    @property
    def entry_definitions(self) -> tuple[ToolDefinition, ...]:
        return self._view.entry_definitions

    def provenance_for(
        self, name: str, args: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._view.provenance_for(name, args)

    def entry_toolset(
        self,
        executor: ToolExecutor,
        event_handler: ToolEventHandler | None = None,
        toolset_id: str = "bifrost-entry",
    ) -> Any:
        return self._view.entry_toolset(executor, event_handler, toolset_id)

    async def _authorize_path(self, path: tuple[UUID, ...]) -> Agent:
        """Reauthorize the caller plus every active delegation edge in ``path``."""
        if not path or path[0] != self.root_id:
            raise AgentCompositionError("Tool path is not rooted in this agent")
        async with self.session_factory() as session:
            try:
                caller, parent = await authorize_agent_execution(session, self.caller, self.root_id)
                for child_id in path[1:]:
                    child = await _load_approved_child(session, parent=parent, child_id=child_id)
                    if child is None:
                        raise AgentCompositionError("Agent delegation is no longer available")
                    caller, parent = await authorize_agent_execution(session, caller, child.id)
                return parent
            except AgentRuntimeAuthorizationError as exc:
                raise AgentCompositionError(str(exc)) from exc

    async def authorize_capability_load(self, capability_id: str) -> None:
        """Reject a deferred capability whose graph path changed before loading."""
        deferred = self._view.deferred_for_capability(capability_id)
        if deferred is None:
            raise AgentCompositionError("Capability is not available")
        await self._authorize_path(deferred.path)

    async def resolve_call(self, name: str, args: dict[str, Any]) -> ResolvedToolCall:
        """Resolve a model-visible name to one fresh owner/tool binding."""
        if name == "delegate_task":
            agent_name = args.get("agent_name")
            task = args.get("task")
            binding = self._view.isolated_delegate_for(agent_name)
            if binding is None or not isinstance(task, str) or not task.strip():
                raise AgentCompositionError("Approved agent_name and non-empty task are required")
            arguments = {"task": task}
        else:
            binding = self._view.binding_for(name)
            if binding is None:
                raise AgentCompositionError(f"Tool '{name}' is not available")
            arguments = args

        owner = await self._authorize_path(binding.path)
        if binding.delegated_target_path is not None:
            await self._authorize_path(binding.delegated_target_path)
        async with self.session_factory() as session:
            definitions, workflow_ids = await resolve_agent_tools(
                owner, session, caller_user_id=self.caller_user_id
            )
        definition = next((item for item in definitions if item.name == binding.original_name), None)
        if definition is None:
            raise AgentCompositionError("Tool is no longer bound to its source agent")
        if definition.parameters != binding.parameters:
            raise AgentCompositionError("Tool schema changed since this run was planned")
        if workflow_ids.get(binding.original_name) != binding.bound_id:
            raise AgentCompositionError("Tool binding changed since this run was planned")
        try:
            require_valid_capability_arguments(definition.parameters, arguments)
        except CapabilityArgumentError as exc:
            if exc.schema_error:
                raise AgentCompositionError(
                    f"The live tool schema is invalid and cannot be executed: {exc.schema_error}"
                ) from exc
            raise AgentCompositionError(
                f"Arguments do not match the live tool schema. Issues: {exc.issues}"
            ) from exc
        if binding.tool_ref is not None:
            route = resolve_capability_route(owner, definition.name, workflow_ids)
            if route is None or capability_ref(owner.id, route) != binding.tool_ref:
                raise AgentCompositionError("Tool identity changed since this run was planned")
        return ResolvedToolCall(
            owner=owner,
            original_name=binding.original_name,
            arguments=arguments,
            workflow_ids=workflow_ids,
            provenance=binding.provenance,
        )

    def capabilities(
        self,
        executor: ToolExecutor,
        event_handler: ToolEventHandler | None = None,
    ) -> list[Any]:
        return self._view.capabilities(executor, event_handler)


def _tool_reference(agent: Agent, name: str, id_map: dict[str, UUID]) -> str | None:
    """Link resolved agent tools to the shared ref; chat-only tools have no MCP ref."""
    route = resolve_capability_route(agent, name, id_map)
    return capability_ref(agent.id, route) if route is not None else None


async def build_agent_composition(
    root: Agent,
    caller: Caller,
    session_factory: Callable[[], Any],
    caller_user_id: UUID | None,
    entry_definitions: Sequence[ToolDefinition],
    entry_workflow_ids: dict[str, UUID],
) -> AgentComposition:
    """Build deferred descendants from the caller-visible active delegation graph."""
    bindings: dict[str, ToolBinding] = {
        definition.name: ToolBinding(
            name=definition.name,
            owner_id=root.id,
            original_name=definition.name,
            path=(root.id,),
            capability_id=None,
            parameters=deepcopy(definition.parameters),
            bound_id=entry_workflow_ids.get(definition.name),
            tool_ref=_tool_reference(root, definition.name, entry_workflow_ids),
        )
        for definition in entry_definitions
    }
    deferred: list[_DeferredAgent] = []
    isolated_delegates: dict[str, ToolBinding] = {}
    definitions_by_agent: dict[UUID, Sequence[ToolDefinition]] = {root.id: entry_definitions}

    async with session_factory() as session:
        refreshed_caller, live_root = await authorize_agent_execution(session, caller, root.id)
        del refreshed_caller
        queue: list[tuple[Agent, tuple[UUID, ...]]] = [(live_root, (live_root.id,))]
        visited = {live_root.id}
        while queue:
            parent, path = queue.pop(0)
            if len(path) - 1 >= 5:
                continue
            children_result = await session.execute(
                select(Agent)
                .join(AgentDelegation, AgentDelegation.child_agent_id == Agent.id)
                .options(
                    selectinload(Agent.tools),
                    selectinload(Agent.delegated_agents),
                    selectinload(Agent.mcp_connections),
                    selectinload(Agent.roles),
                )
                .where(
                    AgentDelegation.parent_agent_id == parent.id,
                    Agent.is_active.is_(True),
                    or_(
                        Agent.organization_id.is_(None),
                        Agent.organization_id == parent.organization_id,
                    ),
                )
                .order_by(Agent.id)
            )
            for child in children_result.scalars():
                if child.id in visited:
                    continue
                try:
                    _, child = await authorize_agent_execution(session, caller, child.id)
                except AgentRuntimeAuthorizationError:
                    continue
                child_path = (*path, child.id)
                definitions, workflow_ids = await resolve_agent_tools(
                    child, session, caller_user_id=caller_user_id
                )
                definitions_by_agent[child.id] = definitions
                capability_id = _capability_id(child.id)
                deferred.append(
                    _DeferredAgent(
                        agent=child,
                        path=child_path,
                        definitions=tuple(
                            ToolDefinition(
                                name=definition.name,
                                description=definition.description,
                                parameters=deepcopy(definition.parameters),
                                return_schema=deepcopy(definition.return_schema),
                                structured_output_schema=deepcopy(definition.structured_output_schema),
                            )
                            for definition in definitions
                        ),
                        capability_id=capability_id,
                    )
                )
                for definition in definitions:
                    qualified_name = _qualified_tool_name(child.id, definition.name)
                    if qualified_name in bindings:
                        raise AgentCompositionError("Specialist tool names are not unique")
                    bindings[qualified_name] = ToolBinding(
                        name=qualified_name,
                        owner_id=child.id,
                        original_name=definition.name,
                        path=child_path,
                        capability_id=capability_id,
                        parameters=deepcopy(definition.parameters),
                        bound_id=workflow_ids.get(definition.name),
                        tool_ref=_tool_reference(child, definition.name, workflow_ids),
                    )
                legacy_name = agent_delegation_slug(child.name)
                legacy_definition = next(
                    (item for item in definitions_by_agent[parent.id] if item.name == legacy_name),
                    None,
                )
                if legacy_definition is not None:
                    delegate_name = _delegate_name(child.id, child.name)
                    isolated_delegates[delegate_name] = ToolBinding(
                        name="delegate_task",
                        owner_id=parent.id,
                        original_name=legacy_name,
                        path=path,
                        capability_id=None,
                        parameters=deepcopy(legacy_definition.parameters),
                        delegated_target_path=child_path,
                    )
                visited.add(child.id)
                queue.append((child, child_path))

    return AgentComposition(
        root_id=root.id,
        caller=caller,
        session_factory=session_factory,
        caller_user_id=caller_user_id,
        instructions=COMPOSITION_INSTRUCTIONS,
        entry_definitions=entry_definitions,
        bindings=bindings,
        deferred=tuple(
            DeferredAgentSnapshot(
                agent_id=item.agent.id,
                name=item.agent.name,
                description=item.agent.description,
                system_prompt=item.agent.system_prompt,
                path=item.path,
                definitions=item.definitions,
                capability_id=item.capability_id,
            )
            for item in deferred
        ),
        isolated_delegates=isolated_delegates,
    )
