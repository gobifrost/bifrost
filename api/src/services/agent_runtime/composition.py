"""Compose one authorized Bifrost agent tree into Pydantic AI capabilities.

The executors remain the single place that performs effects.  This module only
adapts an already-authorized agent graph into deferred Pydantic AI capabilities
and re-checks the exact graph path immediately before an executor dispatches a
tool.  In particular, it never consults the global tool catalog.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any
from uuid import UUID

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import AgentRunResult
from pydantic_ai.agent.wrapper import WrapperAgent
from pydantic_ai.capabilities import AbstractCapability, Capability, ToolSearch
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition as PydanticToolDefinition
from pydantic_ai_harness.subagents import SubAgent, SubAgents
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from src.models.orm.agents import Agent, AgentDelegation
from src.services.agent_runtime.toolset import (
    BifrostToolset,
    ToolEvent,
    ToolEventHandler,
    ToolExecutor,
)
from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.authorization.agent_runtime import (
    AgentRuntimeAuthorizationError,
    authorize_agent_execution,
)
from src.services.authorization.enforce import Caller
from src.services.agent_runtime.graph import MAX_COMPOSITION_DEPTH, load_approved_child
from src.services.execution.agent_helpers import resolve_agent_tools
from src.services.llm import ToolDefinition
from src.services.tool_schema import validate_arguments_against_schema

_SHARED_COMPOSITION_INSTRUCTIONS = """

Keep ownership of this task and its evidence. Use only a tool whose exact callable name and input
schema are supplied by the runtime or returned by discovery. Never guess a tool name, arguments,
or schema.
If discovery or invocation corrects an unknown callable or invalid arguments, use that correction
before making another call.
"""

COMPOSITION_INSTRUCTIONS = _SHARED_COMPOSITION_INSTRUCTIONS + """
For specialist work, load an approved capability's instructions, then search_tools for individual
tools to use in this conversation. Search also discovers the entry agent's tools. Use
same-conversation specialist tools by default. Start an isolated child only when context isolation
or independent review is actually required; never use a child as a permission workaround. Include
the necessary context, inspect the child receipt, and follow up when more information can unblock
it. Respect specialist instructions and approval constraints. Verify uncertain effects before
repeating a write.
"""

DELEGATED_COMPOSITION_INSTRUCTIONS = _SHARED_COMPOSITION_INSTRUCTIONS + """
For specialist work, use an approved tool supplied directly by the runtime. Start an isolated
approved child with delegate_task only when context isolation or independent review is actually
required; never use a child as a permission workaround. Include the necessary context, inspect
the child receipt, and follow up when more information can unblock it. Respect specialist
instructions and approval constraints. Verify uncertain effects before repeating a write.
"""


def composition_instructions(*, automatic: bool) -> str:
    """Return guidance that names only capabilities available in this mode."""
    return COMPOSITION_INSTRUCTIONS if automatic else DELEGATED_COMPOSITION_INSTRUCTIONS


_ACTIVE_SUBAGENT_TOOL_CALL_ID: ContextVar[str] = ContextVar(
    "active_subagent_tool_call_id", default=""
)


class AgentCompositionError(AgentRuntimeAuthorizationError):
    """A requested framework capability or tool is no longer permitted."""


@dataclass(frozen=True)
class ToolBinding:
    """Static identity for one model-visible tool, never an authorization grant."""

    name: str
    owner_id: UUID
    original_name: str
    path: tuple[UUID, ...]
    capability_id: str | None
    parameters: dict[str, Any]
    bound_id: UUID | None = None

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "source_agent_id": str(self.owner_id),
            "source_tool_name": self.original_name,
            "source_path_agent_ids": [str(agent_id) for agent_id in self.path],
            "capability_id": self.capability_id,
        }


@dataclass(frozen=True)
class ChildBinding:
    """One UUID-addressed isolated child, catalog metadata, and approved paths."""

    owner_id: UUID
    parent_path: tuple[UUID, ...]
    target_path: tuple[UUID, ...]
    target_name: str = "Approved child agent"
    target_description: str | None = None

    @property
    def target_agent_id(self) -> UUID:
        return self.target_path[-1]

    @property
    def catalog_description(self) -> str:
        """Human-readable planning metadata for the native SubAgents catalog.

        The UUID catalog key and approved paths remain the only execution
        authority. This metadata only gives the model enough context to choose
        an appropriate isolated child.
        """
        if self.target_description:
            return f"{self.target_name}: {self.target_description}"
        return self.target_name

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "source_agent_id": str(self.owner_id),
            "source_path_agent_ids": [str(agent_id) for agent_id in self.parent_path],
            "delegated_agent_id": str(self.target_agent_id),
            "delegated_target_path_agent_ids": [
                str(agent_id) for agent_id in self.target_path
            ],
        }


@dataclass(frozen=True)
class ResolvedToolCall:
    """A live, exact owner/tool binding approved for one executor dispatch."""

    owner: Agent
    original_name: str
    arguments: dict[str, Any]
    workflow_ids: dict[str, UUID]
    provenance: dict[str, Any]
    delegated_target_id: UUID | None = None


@dataclass(frozen=True)
class _DeferredAgent:
    agent: Agent
    path: tuple[UUID, ...]
    definitions: tuple[ToolDefinition, ...]
    capability_id: str


def _safe_segment(value: str, *, fallback: str) -> str:
    """Return a deterministic Pydantic tool/id-safe segment."""
    normalized = "".join(
        char.lower() if char.isascii() and char.isalnum() else "_" for char in value
    )
    normalized = normalized.strip("_")
    return normalized or fallback


def _capability_id(agent_id: UUID) -> str:
    return f"bifrost_agent_{agent_id.hex}"


def _qualified_tool_name(agent_id: UUID, original_name: str) -> str:
    """Give each descendant tool a stable unique name under Pydantic's limit."""
    owner = agent_id.hex
    source = _safe_segment(original_name, fallback="tool")
    # The UUID component makes distinct same-named source tools impossible to
    # conflate; retaining source text makes persisted steps understandable.
    source_hash = sha256(original_name.encode()).hexdigest()[:10]
    readable = source[: 64 - len(owner) - len(source_hash) - len("bfa___")]
    return f"bfa_{owner}_{readable}_{source_hash}"


class _CompositionAuthorizationCapability(AbstractCapability[object]):
    """Recheck deferred loads and preserve the actual SubAgents call id."""

    def __init__(
        self,
        composition: "AgentComposition",
        event_handler: ToolEventHandler | None,
    ) -> None:
        self._composition = composition
        self._event_handler = event_handler

    async def prepare_tools(
        self, ctx: RunContext[object], tool_defs: list[PydanticToolDefinition],
    ) -> list[PydanticToolDefinition]:
        """Keep child effects under Bifrost's existing per-run effect ordering."""
        del ctx
        return [
            replace(tool, sequential=True)
            if tool.name == "delegate_task" else tool
            for tool in tool_defs
        ]

    async def before_model_request(self, ctx: RunContext[object], request_context: Any) -> Any:
        """Do not send already-loaded specialist instructions after revocation."""
        loaded_ids = set(ctx.loaded_capability_ids)
        for capability_id in loaded_ids:
            await self._composition.authorize_capability_load(capability_id)
        return request_context

    async def before_tool_execute(
        self,
        ctx: RunContext[object],
        *,
        call: ToolCallPart,
        tool_def: PydanticToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        del ctx, tool_def
        if call.tool_name == "load_capability":
            capability_id = args.get("id")
            if not isinstance(capability_id, str):
                raise AgentCompositionError("Capability id is required")
            # The SDK owns correction for an unknown id: its native loader
            # returns ModelRetry with the registered catalog, before any
            # Bifrost effect can run. Only a capability we actually registered
            # receives a live authority check here.
            if self._composition.has_deferred_capability(capability_id):
                await self._composition.authorize_capability_load(capability_id)
        return args

    async def wrap_tool_execute(
        self,
        ctx: RunContext[object],
        *,
        call: ToolCallPart,
        tool_def: PydanticToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> Any:
        del ctx, tool_def
        if call.tool_name not in {"delegate_task", "load_capability", "search_tools"}:
            return await handler(args)
        if self._event_handler:
            await self._event_handler(
                ToolEvent(type="tool_call", tool_name=call.tool_name, arguments=args)
            )
        token = _ACTIVE_SUBAGENT_TOOL_CALL_ID.set(call.tool_call_id or "")
        try:
            result = await handler(args)
        except Exception as exc:
            if self._event_handler:
                await self._event_handler(
                    ToolEvent(
                        type="tool_error",
                        tool_name=call.tool_name,
                        arguments=args,
                        error=str(exc),
                    )
                )
            raise
        finally:
            _ACTIVE_SUBAGENT_TOOL_CALL_ID.reset(token)
        if self._event_handler:
            if call.tool_name == "delegate_task" and isinstance(result, str) and result.startswith("Error:"):
                await self._event_handler(
                    ToolEvent(
                        type="tool_error",
                        tool_name=call.tool_name,
                        arguments=args,
                        error=result,
                    )
                )
                return result
            await self._event_handler(
                ToolEvent(
                    type="tool_result",
                    tool_name=call.tool_name,
                    arguments=args,
                    # ``load_capability`` may return framework state that
                    # includes specialist instructions. Persist only an
                    # auditable receipt, never those instructions.
                    result=(
                        {"capability_id": args.get("id"), "loaded": True}
                        if call.tool_name == "load_capability"
                        else {"searched": True, "query_count": len(args.get("queries", []))}
                        if call.tool_name == "search_tools"
                        else result
                    ),
                )
            )
        return result


class _BifrostChildAgent(WrapperAgent[object, str]):
    """A public WrapperAgent bridge to Bifrost's existing child lifecycle."""

    def __init__(
        self,
        *,
        agent_name: str,
        description: str | None,
        executor: ToolExecutor,
    ) -> None:
        super().__init__(PydanticAgent(name=agent_name, description=description))
        self._delegate_name = agent_name
        self._executor = executor

    async def run(self, user_prompt: Any = None, **_: Any) -> AgentRunResult[str]:
        """Forward exactly one native delegation without a child model request."""
        if isinstance(user_prompt, str):
            task = user_prompt
        else:
            task = str(user_prompt or "")
        try:
            output = await self._executor(
                "delegate_task",
                {"agent_name": self._delegate_name, "task": task},
                _ACTIVE_SUBAGENT_TOOL_CALL_ID.get(),
            )
        except AgentRunCancelled:
            raise
        except Exception as exc:
            # The legacy BifrostToolset turns operational child failures into a
            # model-visible receipt.  Preserve that recovery contract for the
            # native SubAgents bridge instead of aborting the owning run.
            output = f"Error: {exc}"
        return AgentRunResult(output=str(output))


class AgentComposition:
    """An immutable planning snapshot with live authorization at every effect."""

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
        deferred: Sequence[_DeferredAgent],
        isolated_delegates: dict[str, ChildBinding],
        automatic: bool = True,
    ) -> None:
        self.root_id = root_id
        self.caller = caller
        self.session_factory = session_factory
        self.caller_user_id = caller_user_id
        self.instructions = instructions
        self.entry_definitions = tuple(entry_definitions)
        self._bindings = dict(bindings)
        self._deferred = tuple(deferred)
        self._isolated_delegates = dict(isolated_delegates)
        self.automatic = automatic

    def provenance_for(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Return static review metadata without treating it as live authority."""
        if name == "delegate_task":
            agent_name = (args or {}).get("agent_name")
            binding = self._isolated_delegates.get(agent_name)
        else:
            binding = self._bindings.get(name)
        return {} if binding is None else binding.provenance

    def entry_toolset(
        self,
        executor: ToolExecutor,
        event_handler: ToolEventHandler | None = None,
        toolset_id: str = "bifrost-entry",
    ) -> Any:
        """Expose root tools with Automatic discovery or Delegated direct loading."""
        toolset = BifrostToolset(
            self.entry_definitions,
            executor,
            event_handler=event_handler,
            toolset_id=toolset_id,
        )
        return toolset.defer_loading() if self.automatic else toolset

    async def _authorize_path(self, path: tuple[UUID, ...]) -> Agent:
        """Reauthorize the caller plus every active delegation edge in ``path``."""
        if not path or path[0] != self.root_id:
            raise AgentCompositionError("Tool path is not rooted in this agent")
        async with self.session_factory() as session:
            try:
                caller, parent = await authorize_agent_execution(session, self.caller, self.root_id)
                for child_id in path[1:]:
                    child = await load_approved_child(session, parent=parent, child_id=child_id)
                    if child is None:
                        raise AgentCompositionError("Agent delegation is no longer available")
                    caller, parent = await authorize_agent_execution(session, caller, child.id)
                return parent
            except AgentRuntimeAuthorizationError as exc:
                raise AgentCompositionError(str(exc)) from exc

    async def authorize_capability_load(self, capability_id: str) -> None:
        """Reject a deferred capability whose graph path changed before loading."""
        deferred = next((item for item in self._deferred if item.capability_id == capability_id), None)
        if deferred is None:
            raise AgentCompositionError("Capability is not available")
        await self._authorize_path(deferred.path)

    def has_deferred_capability(self, capability_id: str) -> bool:
        """Whether this composition registered the deferred capability id."""
        return any(item.capability_id == capability_id for item in self._deferred)

    async def resolve_call(self, name: str, args: dict[str, Any]) -> ResolvedToolCall:
        """Resolve a model-visible name to one fresh owner/tool binding.

        A stale or revoked graph, a changed tool binding, and a changed schema all
        fail before the executor can perform any side effect.
        """
        if name == "delegate_task":
            agent_name = args.get("agent_name")
            task = args.get("task")
            binding = self._isolated_delegates.get(agent_name)
            if binding is None or not isinstance(task, str) or not task.strip():
                raise AgentCompositionError("Approved agent_name and non-empty task are required")
            owner = await self._authorize_path(binding.parent_path)
            await self._authorize_path(binding.target_path)
            return ResolvedToolCall(
                owner=owner,
                original_name="delegate_task",
                arguments={"task": task},
                workflow_ids={},
                provenance=binding.provenance,
                delegated_target_id=binding.target_agent_id,
            )
        else:
            binding = self._bindings.get(name)
            if binding is None:
                raise AgentCompositionError(f"Tool '{name}' is not available")
            arguments = args

        owner = await self._authorize_path(binding.path)
        async with self.session_factory() as session:
            # Re-resolve the owner's bindings after the graph check. This checks
            # workflow, MCP, and knowledge bindings uniformly.
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
        issues, schema_error = validate_arguments_against_schema(definition.parameters, arguments)
        if schema_error:
            raise AgentCompositionError(
                f"The live tool schema is invalid and cannot be executed: {schema_error}"
            )
        if issues:
            raise AgentCompositionError(
                f"Arguments do not match the live tool schema. Issues: {issues}"
            )
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
        """Build framework capabilities bound to this composition snapshot."""
        capabilities: list[Any] = [_CompositionAuthorizationCapability(self, event_handler)]
        if self.automatic:
            # Pin Pydantic AI's public client-executed strategy. This preserves
            # native provider support while keeping discovery exchanges durable
            # in Bifrost's stable chat history on every provider.
            capabilities.append(ToolSearch(strategy="keywords"))
        specialist_definitions: list[ToolDefinition] = []
        owner_capability_by_tool_name: dict[str, str] = {}
        for deferred in self._deferred if self.automatic else ():
            for definition in deferred.definitions:
                qualified_name = _qualified_tool_name(deferred.agent.id, definition.name)
                binding = self._bindings[qualified_name]
                specialist_definitions.append(
                    ToolDefinition(
                        name=qualified_name,
                        description=(
                            f"{definition.description}\n\n"
                            f"Owner: {deferred.agent.name}. Original tool: {binding.original_name}."
                        ),
                        parameters=definition.parameters,
                    )
                )
                owner_capability_by_tool_name[qualified_name] = deferred.capability_id
            capabilities.append(
                Capability(
                    id=deferred.capability_id,
                    description=(
                        f"Instructions for {deferred.agent.name}. "
                        f"{getattr(deferred.agent, 'description', None) or ''} "
                        "Load only when their specialist capability is needed."
                    ),
                    instructions=(
                        f"{deferred.agent.system_prompt.rstrip()}\n\n"
                        "Search for an approved individual tool only after loading this skill. "
                        "Use it only for work this specialist owns and return evidence to the "
                        "owning conversation."
                    ),
                    defer_loading=True,
                )
            )
        if specialist_definitions:
            def specialist_owner_is_loaded(
                ctx: RunContext[object], tool_def: PydanticToolDefinition,
            ) -> bool:
                capability_id = owner_capability_by_tool_name.get(tool_def.name)
                return capability_id is not None and capability_id in ctx.loaded_capability_ids

            capabilities.append(
                Capability(
                    id="bifrost_specialist_tool_search",
                    toolsets=[
                        BifrostToolset(
                            specialist_definitions,
                            executor,
                            event_handler=event_handler,
                            toolset_id="bifrost-specialist-search",
                        )
                        .filtered(specialist_owner_is_loaded)
                        .defer_loading()
                    ],
                )
            )
        if self._isolated_delegates:
            children: list[SubAgent[object]] = []
            for name, binding in self._isolated_delegates.items():
                children.append(
                    SubAgent(
                        _BifrostChildAgent(
                            agent_name=name,
                            description=binding.catalog_description,
                            executor=executor,
                        ),
                        name=name,
                        description=binding.catalog_description,
                    )
                )
            capabilities.append(
                SubAgents(
                    agents=children,
                    agent_folders=None,
                    forward_usage=True,
                    inherit_tools=False,
                    # Match the owning runtime's one schema/name correction.
                    # Operational child errors are ordinary receipts above,
                    # so this never automatically repeats a business effect.
                    tool_retries=1,
                    max_depth=MAX_COMPOSITION_DEPTH,
                )
            )
        return capabilities


async def build_agent_composition(
    root: Agent,
    caller: Caller,
    session_factory: Callable[[], Any],
    caller_user_id: UUID | None,
    entry_definitions: Sequence[ToolDefinition],
    entry_workflow_ids: dict[str, UUID],
    automatic: bool = True,
) -> AgentComposition:
    """Build approved native children and Automatic specialist capabilities."""
    bindings: dict[str, ToolBinding] = {
        definition.name: ToolBinding(
            name=definition.name,
            owner_id=root.id,
            original_name=definition.name,
            path=(root.id,),
            capability_id=None,
            parameters=deepcopy(definition.parameters),
            bound_id=entry_workflow_ids.get(definition.name),
        )
        for definition in entry_definitions
    }
    deferred: list[_DeferredAgent] = []
    isolated_delegates: dict[str, ChildBinding] = {}

    async with session_factory() as session:
        refreshed_caller, live_root = await authorize_agent_execution(session, caller, root.id)
        del refreshed_caller
        queue: list[tuple[Agent, tuple[UUID, ...]]] = [(live_root, (live_root.id,))]
        visited = {live_root.id}
        while queue:
            parent, path = queue.pop(0)
            if len(path) - 1 >= MAX_COMPOSITION_DEPTH:
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
                if automatic:
                    definitions, workflow_ids = await resolve_agent_tools(
                        child, session, caller_user_id=caller_user_id
                    )
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
                        )
                isolated_delegates[child.id.hex] = ChildBinding(
                    owner_id=parent.id,
                    parent_path=path,
                    target_path=child_path,
                    target_name=child.name,
                    target_description=child.description,
                )
                visited.add(child.id)
                queue.append((child, child_path))

    return AgentComposition(
        root_id=root.id,
        caller=caller,
        session_factory=session_factory,
        caller_user_id=caller_user_id,
        instructions=composition_instructions(automatic=automatic),
        entry_definitions=entry_definitions,
        bindings=bindings,
        deferred=deferred,
        isolated_delegates=isolated_delegates,
        automatic=automatic,
    )
