"""Select the model-loop runtime for one API-owned execution scope."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from src.services.agent_runtime.compute import (
    AgentRuntimePlan,
    BuiltAgentRuntime,
    build_agent_runtime as build_local_agent_runtime,
)

RuntimeBuilder = Callable[..., BuiltAgentRuntime]
_runtime_builder: ContextVar[RuntimeBuilder | None] = ContextVar(
    "api_agent_compute_builder", default=None,
)


@contextmanager
def agent_compute_execution(builder: RuntimeBuilder) -> Iterator[None]:
    """Bind physical compute to this API run and its authorized delegates."""
    token = _runtime_builder.set(builder)
    try:
        yield
    finally:
        _runtime_builder.reset(token)


def build_agent_runtime(plan: AgentRuntimePlan, **callbacks: Any) -> BuiltAgentRuntime:
    builder = _runtime_builder.get()
    if builder is None:
        return build_local_agent_runtime(plan, **callbacks)
    return builder(plan, **callbacks)


def is_agent_compute_execution() -> bool:
    """Whether callbacks belong to an API-owned physical compute scope."""
    return _runtime_builder.get() is not None
