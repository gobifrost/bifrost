"""Shared Pydantic AI runtime primitives for every Bifrost agent surface."""

from src.services.agent_runtime.budgets import AgentRunBudget, build_runtime_capabilities
from src.services.agent_runtime.empty_output import (
    EMPTY_OUTPUT_FALLBACK_MAX_TOKENS,
    EMPTY_OUTPUT_MAX_FALLBACKS,
    EmptyOutputCircuitBreaker,
    empty_output_handoff_text,
    is_blank_text,
    is_empty_no_tool_response,
    response_fingerprint,
)
from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.agent_runtime.model_factory import (
    agent_model_settings,
    agent_model_settings_for_chain,
    create_agent_model,
    provider_name_for_config,
)
from src.services.agent_runtime.model_failover import (
    ChainModel,
    FailoverModel,
    build_chain_model,
    is_failover_eligible,
)
from src.services.agent_runtime.observed_model import ModelCallEvent, ModelCallObserver, ObservedModel
from src.services.agent_runtime.toolset import (
    BifrostToolset,
    ToolEvent,
    ToolEventHandler,
    bound_tool_result_for_model,
)
from src.services.agent_runtime.usage import provider_reported_cost

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
]
