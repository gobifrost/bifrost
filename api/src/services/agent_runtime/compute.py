"""Pure PydanticAI runtime planning and construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.usage import RunUsage

from src.services.agent_runtime.budgets import AgentRunBudget, build_runtime_capabilities
from src.services.agent_runtime.composition_view import (
    AgentCompositionSnapshot,
    AgentCompositionView,
    CapabilityLoadAuthorizer,
    CompositionErrorFactory,
)
from src.services.agent_runtime.empty_output import EmptyOutputCircuitBreaker
from src.services.agent_runtime.tool_policy import build_automatic_code_mode_capabilities
from src.services.agent_runtime.toolset import (
    BifrostToolset,
    ToolEventHandler,
    ToolExecutor,
    code_mode_safe_name_map,
)
from src.services.llm.base import ToolDefinition

RuntimeSurface = Literal["autonomous", "chat"]


@dataclass(frozen=True)
class RuntimeComputeResult:
    output: Any
    empty_output_guard: dict[str, Any] | None


@dataclass(frozen=True)
class RuntimeComputeResultEvent:
    result: RuntimeComputeResult


_PLAN_FIELDS = frozenset({
    "surface", "prompt", "definitions", "composition_snapshot", "max_requests",
    "max_total_tokens", "warning_threshold", "initial_requests", "initial_total_tokens",
    "context_window", "automatic_code_mode", "runtime_id", "toolset_id", "empty_output_guard",
})
_TOOL_FIELDS = frozenset({
    "name", "description", "parameters", "return_schema", "structured_output_schema",
})


def _definition_to_json(definition: ToolDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "description": definition.description,
        "parameters": deepcopy(definition.parameters),
        "return_schema": deepcopy(definition.return_schema),
        "structured_output_schema": deepcopy(definition.structured_output_schema),
    }


def _definition_from_json(value: Mapping[str, Any]) -> ToolDefinition:
    if set(value) != _TOOL_FIELDS:
        raise ValueError("Invalid tool-definition fields in runtime plan")
    if not isinstance(value["name"], str) or not isinstance(value["description"], str):
        raise ValueError("Runtime-plan tool name and description must be strings")
    if not isinstance(value["parameters"], dict):
        raise ValueError("Runtime-plan tool parameters must be an object")
    return ToolDefinition(
        name=value["name"], description=value["description"],
        parameters=deepcopy(value["parameters"]),
        return_schema=deepcopy(value["return_schema"]),
        structured_output_schema=deepcopy(value["structured_output_schema"]),
    )


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Runtime-plan {field} must be an integer or null")
    return value


@dataclass(frozen=True)
class AgentRuntimePlan:
    """JSON-safe model-loop inputs, excluding profile settings and live usage."""

    surface: RuntimeSurface
    prompt: str
    definitions: tuple[ToolDefinition, ...]
    composition_snapshot: AgentCompositionSnapshot | None
    max_requests: int | None
    max_total_tokens: int | None
    warning_threshold: float
    initial_requests: int
    initial_total_tokens: int
    context_window: int | None
    automatic_code_mode: bool
    runtime_id: str
    toolset_id: str
    empty_output_guard: bool

    @property
    def budget(self) -> AgentRunBudget:
        return AgentRunBudget(
            max_requests=self.max_requests,
            max_total_tokens=self.max_total_tokens,
            warning_threshold=self.warning_threshold,
            initial_requests=self.initial_requests,
            initial_total_tokens=self.initial_total_tokens,
        )

    @classmethod
    def from_budget(
        cls,
        *,
        surface: RuntimeSurface,
        prompt: str,
        definitions: Sequence[ToolDefinition],
        composition_snapshot: AgentCompositionSnapshot | None,
        budget: AgentRunBudget,
        context_window: int | None,
        automatic_code_mode: bool,
        runtime_id: str,
        toolset_id: str,
        empty_output_guard: bool,
    ) -> AgentRuntimePlan:
        return cls(
            surface=surface,
            prompt=prompt,
            definitions=tuple(
                _definition_from_json(_definition_to_json(item)) for item in definitions
            ),
            composition_snapshot=composition_snapshot,
            max_requests=budget.max_requests,
            max_total_tokens=budget.max_total_tokens,
            warning_threshold=budget.warning_threshold,
            initial_requests=budget.initial_requests,
            initial_total_tokens=budget.initial_total_tokens,
            context_window=context_window,
            automatic_code_mode=automatic_code_mode,
            runtime_id=runtime_id,
            toolset_id=toolset_id,
            empty_output_guard=empty_output_guard,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "prompt": self.prompt,
            "definitions": [_definition_to_json(item) for item in self.definitions],
            "composition_snapshot": (
                self.composition_snapshot.to_json()
                if self.composition_snapshot is not None
                else None
            ),
            "max_requests": self.max_requests,
            "max_total_tokens": self.max_total_tokens,
            "warning_threshold": self.warning_threshold,
            "initial_requests": self.initial_requests,
            "initial_total_tokens": self.initial_total_tokens,
            "context_window": self.context_window,
            "automatic_code_mode": self.automatic_code_mode,
            "runtime_id": self.runtime_id,
            "toolset_id": self.toolset_id,
            "empty_output_guard": self.empty_output_guard,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> AgentRuntimePlan:
        unexpected, missing = set(value) - _PLAN_FIELDS, _PLAN_FIELDS - set(value)
        if unexpected:
            raise ValueError(f"Unexpected runtime-plan fields: {sorted(unexpected)}")
        if missing:
            raise ValueError(f"Missing runtime-plan fields: {sorted(missing)}")
        surface = value["surface"]
        definitions = value["definitions"]
        snapshot = value["composition_snapshot"]
        if surface not in {"autonomous", "chat"}:
            raise ValueError("Runtime-plan surface must be autonomous or chat")
        if not isinstance(value["prompt"], str) or not isinstance(definitions, list):
            raise ValueError("Runtime-plan prompt and definitions have invalid types")
        if not all(isinstance(item, Mapping) for item in definitions):
            raise ValueError("Runtime-plan definitions must contain objects")
        if snapshot is not None and not isinstance(snapshot, Mapping):
            raise ValueError("Runtime-plan composition snapshot must be an object or null")
        if (
            not isinstance(value["warning_threshold"], (int, float))
            or isinstance(value["warning_threshold"], bool)
        ):
            raise ValueError("Runtime-plan warning threshold must be numeric")
        for field in ("initial_requests", "initial_total_tokens"):
            if isinstance(value[field], bool) or not isinstance(value[field], int):
                raise ValueError(f"Runtime-plan {field} must be an integer")
        for field in ("automatic_code_mode", "empty_output_guard"):
            if not isinstance(value[field], bool):
                raise ValueError(f"Runtime-plan {field} must be boolean")
        for field in ("runtime_id", "toolset_id"):
            if not isinstance(value[field], str):
                raise ValueError(f"Runtime-plan {field} must be a string")
        composition_snapshot = None
        if snapshot is not None:
            composition_snapshot = AgentCompositionSnapshot.from_json(snapshot)
            if composition_snapshot.to_json() != dict(snapshot):
                raise ValueError("Runtime-plan composition snapshot is not canonical JSON")
        return cls(
            surface=surface,
            prompt=value["prompt"],
            definitions=tuple(_definition_from_json(item) for item in definitions),
            composition_snapshot=composition_snapshot,
            max_requests=_optional_int(value["max_requests"], "max_requests"),
            max_total_tokens=_optional_int(value["max_total_tokens"], "max_total_tokens"),
            warning_threshold=float(value["warning_threshold"]),
            initial_requests=value["initial_requests"],
            initial_total_tokens=value["initial_total_tokens"],
            context_window=_optional_int(value["context_window"], "context_window"),
            automatic_code_mode=value["automatic_code_mode"],
            runtime_id=value["runtime_id"],
            toolset_id=value["toolset_id"],
            empty_output_guard=value["empty_output_guard"],
        )


@dataclass(frozen=True)
class BuiltAgentRuntime:
    """Live objects intentionally excluded from ``AgentRuntimePlan``."""

    runtime: Any
    usage: RunUsage
    empty_output_guard: EmptyOutputCircuitBreaker | None
    composition_view: AgentCompositionView | None
    toolset: Any


def build_agent_runtime(
    plan: AgentRuntimePlan,
    *,
    model: Any,
    executor: ToolExecutor,
    event_handler: ToolEventHandler | None,
    model_settings: Any,
    usage: RunUsage | None = None,
    capability_load_authorizer: CapabilityLoadAuthorizer | None = None,
    composition_error_type: CompositionErrorFactory | None = None,
    empty_output_guard: EmptyOutputCircuitBreaker | None = None,
) -> BuiltAgentRuntime:
    """Build the existing PydanticAI policy from a plan and live callbacks."""
    view = None
    if plan.composition_snapshot is not None:
        if capability_load_authorizer is None or composition_error_type is None:
            raise ValueError("A composition snapshot requires live authorization callbacks")
        view = AgentCompositionView(
            plan.composition_snapshot,
            authorize_capability_load=capability_load_authorizer,
            error_type=composition_error_type,
        )
    if plan.empty_output_guard:
        guard = empty_output_guard or EmptyOutputCircuitBreaker()
    elif empty_output_guard is not None:
        raise ValueError("Empty-output guard was supplied for a plan that disables it")
    else:
        guard = None
    toolset = (
        view.entry_toolset(
            executor, event_handler=event_handler, toolset_id=plan.toolset_id
        )
        if view is not None
        else BifrostToolset(
            plan.definitions,
            executor,
            event_handler=event_handler,
            toolset_id=plan.toolset_id,
        )
    )
    if plan.automatic_code_mode and view is None:
        toolset = toolset.renamed(
            code_mode_safe_name_map(tuple(item.name for item in plan.definitions))
        )
    capabilities: list[Any] = [
        *build_runtime_capabilities(plan.budget, context_window=plan.context_window)
    ]
    if guard is not None:
        capabilities.append(guard)
    if view is not None:
        capabilities.extend(view.capabilities(executor, event_handler))
    if plan.automatic_code_mode:
        capabilities.extend(build_automatic_code_mode_capabilities())
    kwargs: dict[str, Any] = {
        "toolsets": [toolset] if plan.definitions else [],
        "capabilities": capabilities,
        "model_settings": model_settings,
        "retries": 1,
        "end_strategy": "exhaustive",
    }
    kwargs["instructions" if plan.surface == "chat" else "system_prompt"] = plan.prompt
    return BuiltAgentRuntime(
        runtime=PydanticAgent(model, **kwargs),
        usage=usage if usage is not None else RunUsage(),
        empty_output_guard=guard,
        composition_view=view,
        toolset=toolset,
    )
