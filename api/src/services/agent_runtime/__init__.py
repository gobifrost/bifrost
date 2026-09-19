"""Shared durable agent-runtime primitives for every Bifrost agent surface.

Only loop- and import-safe helpers (``errors``, ``types``, ``settings``) are
imported eagerly. Everything Pydantic AI-backed (toolsets, budgets, models,
the checkpoint codec, resume, delegation, timers, the debugger) resolves
lazily through :func:`__getattr__` so merely importing this package — as the
worker entry closure does via the agent-run consumer — never pulls
``pydantic_ai`` (and transitively ``mcp``/``uvicorn``/``starlette``) into
``sys.modules``. See ``test_worker_app_closure_has_no_heavyweights``.
"""

from src.services.agent_runtime import settings as settings
from src.services.agent_runtime import types as types
from src.services.agent_runtime.errors import AgentRunCancelled

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # Static types only; runtime resolves via __getattr__.
    from src.services.agent_runtime.budgets import (  # noqa: F401
        AgentRunBudget,
        build_runtime_capabilities,
    )
    from src.services.agent_runtime.empty_output import (  # noqa: F401
        EMPTY_OUTPUT_FALLBACK_MAX_TOKENS,
        EMPTY_OUTPUT_MAX_FALLBACKS,
        EmptyOutputCircuitBreaker,
        empty_output_handoff_text,
        is_blank_text,
        is_empty_no_tool_response,
        response_fingerprint,
    )
    from src.services.agent_runtime.model_factory import (  # noqa: F401
        agent_model_settings,
        agent_model_settings_for_chain,
        create_agent_model,
        provider_name_for_config,
    )
    from src.services.agent_runtime.model_failover import (  # noqa: F401
        ChainModel,
        FailoverModel,
        build_chain_model,
        is_failover_eligible,
    )
    from src.services.agent_runtime.observed_model import (  # noqa: F401
        ModelCallEvent,
        ModelCallObserver,
        ObservedModel,
    )
    from src.services.agent_runtime.toolset import (  # noqa: F401
        BifrostToolset,
        ToolEvent,
        ToolEventHandler,
        bound_tool_result_for_model,
    )
    from src.services.agent_runtime.usage import provider_reported_cost  # noqa: F401

__all__ = [
    "AgentRunBudget",
    "AgentRunCancelled",
    "BifrostToolset",
    "ChainModel",
    "EMPTY_OUTPUT_FALLBACK_MAX_TOKENS",
    "EMPTY_OUTPUT_MAX_FALLBACKS",
    "EmptyOutputCircuitBreaker",
    "FailoverModel",
    "ModelCallEvent",
    "ModelCallObserver",
    "ObservedModel",
    "ToolEvent",
    "ToolEventHandler",
    "bound_tool_result_for_model",
    "build_chain_model",
    "build_runtime_capabilities",
    "agent_model_settings",
    "agent_model_settings_for_chain",
    "create_agent_model",
    "empty_output_handoff_text",
    "is_blank_text",
    "is_empty_no_tool_response",
    "is_failover_eligible",
    "provider_name_for_config",
    "provider_reported_cost",
    "response_fingerprint",
    "settings",
    "types",
]

_LAZY_ATTRS: dict[str, str] = {
    # Heavy Pydantic AI-backed helpers.
    "AgentRunBudget": "budgets",
    "build_runtime_capabilities": "budgets",
    "EMPTY_OUTPUT_FALLBACK_MAX_TOKENS": "empty_output",
    "EMPTY_OUTPUT_MAX_FALLBACKS": "empty_output",
    "EmptyOutputCircuitBreaker": "empty_output",
    "empty_output_handoff_text": "empty_output",
    "is_blank_text": "empty_output",
    "is_empty_no_tool_response": "empty_output",
    "response_fingerprint": "empty_output",
    "agent_model_settings": "model_factory",
    "agent_model_settings_for_chain": "model_factory",
    "create_agent_model": "model_factory",
    "provider_name_for_config": "model_factory",
    "ChainModel": "model_failover",
    "FailoverModel": "model_failover",
    "build_chain_model": "model_failover",
    "is_failover_eligible": "model_failover",
    "ModelCallEvent": "observed_model",
    "ModelCallObserver": "observed_model",
    "ObservedModel": "observed_model",
    "BifrostToolset": "toolset",
    "ToolEvent": "toolset",
    "ToolEventHandler": "toolset",
    "bound_tool_result_for_model": "toolset",
    "provider_reported_cost": "usage",
    # Durable state submodules (import-safe, resolved on demand so the
    # package import itself stays minimal).
    "budgets": "budgets",
    "checkpoint_codec": "checkpoint_codec",
    "debugger": "debugger",
    "delegation": "delegation",
    "empty_output": "empty_output",
    "errors": "errors",
    "execution_snapshot": "execution_snapshot",
    "model_factory": "model_factory",
    "model_failover": "model_failover",
    "observed_model": "observed_model",
    "output_contract": "output_contract",
    "resume": "resume",
    "run_store": "run_store",
    "timers": "timers",
    "tool_invocations": "tool_invocations",
    "toolset": "toolset",
    "usage": "usage",
}


def __getattr__(name: str):
    """Resolve heavy helpers and submodules on first attribute access."""
    if name in _LAZY_ATTRS:
        import importlib

        module = importlib.import_module(f"{__name__}.{_LAZY_ATTRS[name]}")
        value = getattr(module, name) if _LAZY_ATTRS[name] != name else module
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__) | set(_LAZY_ATTRS))
