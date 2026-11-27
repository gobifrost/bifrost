"""Pure model-visible view of an authorized agent composition snapshot.

This module deliberately knows nothing about ORM state, callers, or live
authorization.  Its snapshot can cross a process boundary; the embedding
runtime supplies fresh authorization and effect callbacks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any
from uuid import UUID

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import AgentRunResult
from pydantic_ai.agent.wrapper import WrapperAgent
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition as PydanticToolDefinition
from pydantic_ai_harness.subagents import SubAgent, SubAgents

from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.agent_runtime.tool_policy import build_bifrost_tool_search
from src.services.agent_runtime.toolset import (
    BifrostToolset,
    ToolEvent,
    ToolEventHandler,
    ToolExecutor,
    code_mode_safe_name_map,
)
from src.services.llm.base import ToolDefinition

MAX_COMPOSITION_DEPTH = 5

COMPOSITION_INSTRUCTIONS = """

Keep ownership of this task and its evidence. Use only a tool whose exact callable name and input
schema are supplied by the runtime or returned by discovery. Never guess a tool name, arguments,
or schema.
If discovery or invocation corrects an unknown callable or invalid arguments, use that correction
before making another call.

For specialist work, load an approved capability's instructions, discover its approved tools, then
use run_code to combine related calls. Use same-conversation specialist tools by default. Start an isolated child only when context isolation
or independent review is actually required; never use a child as a permission workaround. Include
the necessary context, inspect the child receipt, and follow up when more information can unblock
it. Respect specialist instructions and approval constraints. Verify uncertain effects before
repeating a write.
"""

_ACTIVE_SUBAGENT_TOOL_CALL_ID: ContextVar[str] = ContextVar(
    "active_subagent_tool_call_id", default=""
)

CompositionErrorFactory = Callable[[str], Exception]
CapabilityLoadAuthorizer = Callable[[str], Awaitable[None]]


def _tool_definition_json(definition: ToolDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "description": definition.description,
        "parameters": deepcopy(definition.parameters),
        "return_schema": deepcopy(definition.return_schema),
        "structured_output_schema": deepcopy(definition.structured_output_schema),
    }


def _tool_definition_from_json(value: Mapping[str, Any]) -> ToolDefinition:
    return ToolDefinition(
        name=str(value["name"]),
        description=str(value["description"]),
        parameters=deepcopy(value["parameters"]),
        return_schema=deepcopy(value.get("return_schema")),
        structured_output_schema=deepcopy(value.get("structured_output_schema")),
    )


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
    delegated_target_path: tuple[UUID, ...] | None = None
    tool_ref: str | None = None

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "source_agent_id": str(self.owner_id),
            "source_tool_name": self.original_name,
            "source_path_agent_ids": [str(agent_id) for agent_id in self.path],
            "capability_id": self.capability_id,
            **(
                {
                    "delegated_agent_id": str(self.delegated_target_path[-1]),
                    "delegated_target_path_agent_ids": [
                        str(agent_id) for agent_id in self.delegated_target_path
                    ],
                }
                if self.delegated_target_path
                else {}
            ),
        }

    def to_json(self) -> dict[str, Any]:
        """Return the binding with every UUID converted for control transport."""
        return {
            "name": self.name,
            "owner_id": str(self.owner_id),
            "original_name": self.original_name,
            "path": [str(agent_id) for agent_id in self.path],
            "capability_id": self.capability_id,
            "parameters": deepcopy(self.parameters),
            "bound_id": str(self.bound_id) if self.bound_id else None,
            "delegated_target_path": (
                [str(agent_id) for agent_id in self.delegated_target_path]
                if self.delegated_target_path
                else None
            ),
            "tool_ref": self.tool_ref,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ToolBinding:
        """Restore one transport binding without attaching any live authority."""
        delegated_target_path = value.get("delegated_target_path")
        return cls(
            name=str(value["name"]),
            owner_id=UUID(str(value["owner_id"])),
            original_name=str(value["original_name"]),
            path=tuple(UUID(str(agent_id)) for agent_id in value["path"]),
            capability_id=(
                str(value["capability_id"])
                if value.get("capability_id") is not None
                else None
            ),
            parameters=deepcopy(value["parameters"]),
            bound_id=UUID(str(value["bound_id"])) if value.get("bound_id") else None,
            delegated_target_path=(
                tuple(UUID(str(agent_id)) for agent_id in delegated_target_path)
                if delegated_target_path is not None
                else None
            ),
            tool_ref=str(value["tool_ref"]) if value.get("tool_ref") is not None else None,
        )


@dataclass(frozen=True)
class DeferredAgentSnapshot:
    """Public, model-visible descendant fields with no ORM object attached."""

    agent_id: UUID
    name: str
    description: str | None
    system_prompt: str
    path: tuple[UUID, ...]
    definitions: tuple[ToolDefinition, ...]
    capability_id: str

    def to_json(self) -> dict[str, Any]:
        return {
            "agent_id": str(self.agent_id),
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "path": [str(agent_id) for agent_id in self.path],
            "definitions": [_tool_definition_json(definition) for definition in self.definitions],
            "capability_id": self.capability_id,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> DeferredAgentSnapshot:
        return cls(
            agent_id=UUID(str(value["agent_id"])),
            name=str(value["name"]),
            description=(str(value["description"]) if value.get("description") is not None else None),
            system_prompt=str(value["system_prompt"]),
            path=tuple(UUID(str(agent_id)) for agent_id in value["path"]),
            definitions=tuple(
                _tool_definition_from_json(definition) for definition in value["definitions"]
            ),
            capability_id=str(value["capability_id"]),
        )


@dataclass(frozen=True)
class AgentCompositionSnapshot:
    """Serializable, immutable planning data visible to the model runtime."""

    root_id: UUID
    instructions: str
    entry_definitions: tuple[ToolDefinition, ...]
    bindings: tuple[ToolBinding, ...]
    deferred: tuple[DeferredAgentSnapshot, ...]
    isolated_delegates: tuple[tuple[str, ToolBinding], ...]

    @classmethod
    def from_parts(
        cls,
        *,
        root_id: UUID,
        instructions: str,
        entry_definitions: Sequence[ToolDefinition],
        bindings: Mapping[str, ToolBinding],
        deferred: Sequence[DeferredAgentSnapshot],
        isolated_delegates: Mapping[str, ToolBinding],
    ) -> AgentCompositionSnapshot:
        """Copy planning inputs before an execution can mutate its source graph."""
        return cls(
            root_id=root_id,
            instructions=instructions,
            entry_definitions=tuple(
                _tool_definition_from_json(_tool_definition_json(definition))
                for definition in entry_definitions
            ),
            bindings=tuple(ToolBinding.from_json(binding.to_json()) for binding in bindings.values()),
            deferred=tuple(
                DeferredAgentSnapshot.from_json(item.to_json()) for item in deferred
            ),
            isolated_delegates=tuple(
                (name, ToolBinding.from_json(binding.to_json()))
                for name, binding in isolated_delegates.items()
            ),
        )

    def to_json(self) -> dict[str, Any]:
        """Return JSON-safe bootstrap data with no caller, session, or credentials."""
        return {
            "root_id": str(self.root_id),
            "instructions": self.instructions,
            "entry_definitions": [
                _tool_definition_json(definition) for definition in self.entry_definitions
            ],
            "bindings": [binding.to_json() for binding in self.bindings],
            "deferred": [item.to_json() for item in self.deferred],
            "isolated_delegates": [
                {"name": name, "binding": binding.to_json()}
                for name, binding in self.isolated_delegates
            ],
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> AgentCompositionSnapshot:
        return cls(
            root_id=UUID(str(value["root_id"])),
            instructions=str(value["instructions"]),
            entry_definitions=tuple(
                _tool_definition_from_json(definition) for definition in value["entry_definitions"]
            ),
            bindings=tuple(ToolBinding.from_json(binding) for binding in value["bindings"]),
            deferred=tuple(DeferredAgentSnapshot.from_json(item) for item in value["deferred"]),
            isolated_delegates=tuple(
                (str(item["name"]), ToolBinding.from_json(item["binding"]))
                for item in value["isolated_delegates"]
            ),
        )


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
    source_hash = sha256(original_name.encode()).hexdigest()[:10]
    readable = source[: 64 - len(owner) - len(source_hash) - len("bfa___")]
    return f"bfa_{owner}_{readable}_{source_hash}"


def _delegate_name(agent_id: UUID, agent_name: str) -> str:
    """A unique SubAgents key; names are presentation only, never lookup fallbacks."""
    return f"{_safe_segment(agent_name, fallback='agent')[:31]}_{agent_id.hex}"


def _model_visible_definitions(
    definitions: Sequence[ToolDefinition],
) -> tuple[ToolDefinition, ...]:
    """Hide legacy delegate aliases while retaining their native subagent bindings."""
    return tuple(
        definition
        for definition in definitions
        if not definition.name.startswith("delegate_to_")
    )


class _CompositionAuthorizationCapability(AbstractCapability[object]):
    """Recheck deferred loads and preserve the actual SubAgents call id."""

    def __init__(
        self,
        view: AgentCompositionView,
        event_handler: ToolEventHandler | None,
    ) -> None:
        self._view = view
        self._event_handler = event_handler

    async def prepare_tools(
        self, ctx: RunContext[object], tool_defs: list[PydanticToolDefinition],
    ) -> list[PydanticToolDefinition]:
        del ctx
        return [
            replace(tool, sequential=True)
            if tool.name == "delegate_task" else tool
            for tool in tool_defs
        ]

    async def before_model_request(self, ctx: RunContext[object], request_context: Any) -> Any:
        for capability_id in set(ctx.loaded_capability_ids):
            await self._view.authorize_capability_load(capability_id)
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
                raise self._view.error("Capability id is required")
            if self._view.has_deferred_capability(capability_id):
                await self._view.authorize_capability_load(capability_id)
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
        task = user_prompt if isinstance(user_prompt, str) else str(user_prompt or "")
        try:
            output = await self._executor(
                "delegate_task",
                {"agent_name": self._delegate_name, "task": task},
                _ACTIVE_SUBAGENT_TOOL_CALL_ID.get(),
            )
        except AgentRunCancelled:
            raise
        except Exception as exc:
            output = f"Error: {exc}"
        return AgentRunResult(output=str(output))


class AgentCompositionView:
    """Build PydanticAI capabilities from a snapshot plus live callbacks."""

    def __init__(
        self,
        snapshot: AgentCompositionSnapshot,
        *,
        authorize_capability_load: CapabilityLoadAuthorizer,
        error_type: CompositionErrorFactory,
    ) -> None:
        self.snapshot = snapshot
        self._authorize_capability_load = authorize_capability_load
        self._error_type = error_type
        self._bindings = {binding.name: binding for binding in snapshot.bindings}
        self._deferred = tuple(snapshot.deferred)
        self._deferred_by_capability = {
            item.capability_id: item for item in self._deferred
        }
        self._isolated_delegates = dict(snapshot.isolated_delegates)
        self._code_mode_name_map = code_mode_safe_name_map(tuple(self._bindings))

    @property
    def root_id(self) -> UUID:
        return self.snapshot.root_id

    @property
    def instructions(self) -> str:
        return self.snapshot.instructions

    @property
    def entry_definitions(self) -> tuple[ToolDefinition, ...]:
        return self.snapshot.entry_definitions

    def error(self, message: str) -> Exception:
        return self._error_type(message)

    def binding_for(self, name: str) -> ToolBinding | None:
        return self._bindings.get(name)

    def isolated_delegate_for(self, name: str | None) -> ToolBinding | None:
        return self._isolated_delegates.get(name)

    def deferred_for_capability(self, capability_id: str) -> DeferredAgentSnapshot | None:
        return self._deferred_by_capability.get(capability_id)

    def provenance_for(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any] | None:
        binding = (
            self.isolated_delegate_for((args or {}).get("agent_name"))
            if name == "delegate_task"
            else self.binding_for(name)
        )
        return {} if binding is None else binding.provenance

    async def authorize_capability_load(self, capability_id: str) -> None:
        if self.deferred_for_capability(capability_id) is None:
            raise self.error("Capability is not available")
        await self._authorize_capability_load(capability_id)

    def has_deferred_capability(self, capability_id: str) -> bool:
        return capability_id in self._deferred_by_capability

    def entry_toolset(
        self,
        executor: ToolExecutor,
        event_handler: ToolEventHandler | None = None,
        toolset_id: str = "bifrost-entry",
    ) -> Any:
        toolset = BifrostToolset(
            _model_visible_definitions(self.entry_definitions),
            executor,
            event_handler=event_handler,
            toolset_id=toolset_id,
            tool_metadata=self._tool_metadata(),
        ).defer_loading()
        return toolset.renamed(self._code_mode_name_map)

    def _tool_metadata(self) -> dict[str, dict[str, str]]:
        descriptions = {
            (self.root_id, definition.name): definition.description
            for definition in self.entry_definitions
        }
        descriptions.update({
            (deferred.agent_id, definition.name): definition.description
            for deferred in self._deferred
            for definition in deferred.definitions
        })
        return {
            name: {
                "bifrost_original_name": binding.original_name,
                "bifrost_search_path": f"{_capability_id(binding.owner_id)}.{binding.original_name}",
                "bifrost_tool_description": descriptions.get((binding.owner_id, binding.original_name), ""),
                "bifrost_connector": _capability_id(binding.owner_id),
            }
            for name, binding in self._bindings.items()
        }

    def capabilities(
        self,
        executor: ToolExecutor,
        event_handler: ToolEventHandler | None = None,
    ) -> list[Any]:
        capabilities: list[Any] = [
            _CompositionAuthorizationCapability(self, event_handler),
            build_bifrost_tool_search(),
        ]
        specialist_definitions: list[ToolDefinition] = []
        owner_capability_by_tool_name: dict[str, str] = {}
        for deferred in self._deferred:
            for definition in _model_visible_definitions(deferred.definitions):
                qualified_name = _qualified_tool_name(deferred.agent_id, definition.name)
                binding = self._bindings[qualified_name]
                specialist_definitions.append(
                    ToolDefinition(
                        name=qualified_name,
                        description=(
                            f"{definition.description}\n\n"
                            f"Owner: {deferred.name}. Original tool: {binding.original_name}."
                        ),
                        parameters=definition.parameters,
                        return_schema=deepcopy(definition.return_schema),
                        structured_output_schema=deepcopy(definition.structured_output_schema),
                    )
                )
                owner_capability_by_tool_name[qualified_name] = deferred.capability_id
            capabilities.append(
                Capability(
                    id=deferred.capability_id,
                    description=(
                        f"Instructions for {deferred.name}. "
                        f"{deferred.description or ''} "
                        "Load only when their specialist capability is needed."
                    ),
                    instructions=(
                        f"{deferred.system_prompt.rstrip()}\n\n"
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
                            tool_metadata=self._tool_metadata(),
                        )
                        .filtered(specialist_owner_is_loaded)
                        .defer_loading()
                        .renamed(self._code_mode_name_map)
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
                            description=(
                                "Runs this approved Bifrost agent through its isolated child lifecycle."
                            ),
                            executor=executor,
                        ),
                        name=name,
                        description=(
                            "Optional isolated run of approved agent "
                            f"{binding.delegated_target_path[-1] if binding.delegated_target_path else binding.owner_id}."
                        ),
                    )
                )
            capabilities.append(
                SubAgents(
                    agents=children,
                    agent_folders=None,
                    forward_usage=True,
                    inherit_tools=False,
                    tool_retries=1,
                    max_depth=MAX_COMPOSITION_DEPTH,
                )
            )
        return capabilities
