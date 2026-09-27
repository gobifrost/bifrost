"""Per-request model failover across an operator-configured profile chain.

Failover fires only for transport-terminal retryable failures: the provider
request already exhausted the bounded transport budget (6 attempts / 60s in
``retry_transport``) and came back with a retryable identity (429, 5xx,
timeout, connection error). Everything else — 4xx, validation, token-limit
truncation, budget, cancellation — is terminal on the primary and never
switches candidates. See ``docs/architecture/pydantic-ai-runtime-recovery.md``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext

from src.services.agent_runtime.observed_model import (
    ModelCallObserver,
    ObservedModel,
)
from src.services.llm.base import LLMConfig

logger = logging.getLogger(__name__)

FAILOVERABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
"""HTTP statuses that may fail over after the transport budget is exhausted."""


def is_failover_eligible(exc: BaseException) -> bool:
    """Return True when a model-call failure may try the next chain candidate.

    Duck-typed on ``status_code`` so provider SDK errors (OpenAI,
    Anthropic, Pydantic AI's ``ModelHTTPError``) classify without importing
    provider SDKs at module scope. Connection/timeout errors match their SDK
    classes via lazy import to preserve the worker import boundary.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, bool):
        return False
    if isinstance(status, int):
        return status in FAILOVERABLE_STATUS_CODES
    return _is_connection_error(exc)


def _is_connection_error(exc: BaseException) -> bool:
    """Match provider-SDK connection/timeout errors without module-scope imports."""
    try:
        from anthropic import (
            APIConnectionError as AnthropicConnectionError,
        )
        from anthropic import (
            APITimeoutError as AnthropicTimeoutError,
        )
        from openai import (
            APIConnectionError as OpenAIConnectionError,
        )
        from openai import (
            APITimeoutError as OpenAPITimeoutError,
        )
    except ImportError:  # pragma: no cover - SDKs ship with the API image
        return False
    try:
        import httpx2

        transport_errors: tuple[type[BaseException], ...] = (
            httpx2.TransportError,
        )
    except ImportError:  # pragma: no cover - httpx2 ships with the API image
        transport_errors = ()
    return isinstance(
        exc,
        (
            OpenAIConnectionError,
            OpenAPITimeoutError,
            AnthropicConnectionError,
            AnthropicTimeoutError,
            *transport_errors,
        ),
    )


class FailoverModel(WrapperModel):
    """Try chain candidates in order, advancing past eligible failures.

    ``candidates[0]`` is the primary; each entry is typically an
    ``ObservedModel`` so every attempt keeps its own usage/observer events.
    ``candidate_settings[i]`` replaces the agent-level settings for candidate
    ``i`` when provided (per-profile max-tokens and provider flags differ per
    chain member); ``None`` entries pass the caller's settings through.

    Stickiness: once a request switches candidates, subsequent requests in
    this run start at the new candidate instead of flapping back to a
    provider that just proved sick. ``self.wrapped`` tracks the current
    candidate so ``model_name``/``system``/``provider`` always describe the
    model that will serve (or served) the next request.
    """

    def __init__(
        self,
        candidates: list[Model],
        *,
        candidate_settings: list[ModelSettings | None] | None = None,
    ) -> None:
        if not candidates:
            raise ValueError("FailoverModel needs at least one candidate")
        if candidate_settings is not None and len(candidate_settings) != len(
            candidates
        ):
            raise ValueError("candidate_settings must align with candidates")
        super().__init__(candidates[0])
        self._candidates = list(candidates)
        self._candidate_settings: list[ModelSettings | None] = (
            list(candidate_settings)
            if candidate_settings is not None
            else [None] * len(candidates)
        )
        self._index = 0
        self._switches: list[tuple[str, str]] = []

    @property
    def candidates(self) -> list[Model]:
        """Chain candidates in try order (primary first)."""
        return list(self._candidates)

    @property
    def switches(self) -> list[tuple[str, str]]:
        """(from_model, to_model) pairs switched so far this run."""
        return list(self._switches)

    def fallback_path(self) -> list[str]:
        """Distinct fallback model names actually used, in first-use order."""
        path: list[str] = []
        for _, to_model in self._switches:
            if to_model not in path:
                path.append(to_model)
        return path

    async def __aenter__(self) -> FailoverModel:
        for candidate in self._candidates:
            await candidate.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool | None:
        result: bool | None = None
        for candidate in self._candidates:
            exited = await candidate.__aexit__(exc_type, exc_val, exc_tb)
            result = exited or result
        return result

    def _settings_for(self, index: int, model_settings: ModelSettings | None) -> ModelSettings | None:
        override = self._candidate_settings[index]
        return override if override is not None else model_settings

    def _advance(self, failed_index: int, exc: BaseException) -> int:
        """Record a switch and sync ``wrapped`` to the next candidate."""
        from_model = self._candidates[failed_index].model_name
        next_index = failed_index + 1
        to_model = self._candidates[next_index].model_name
        self._switches.append((from_model, to_model))
        self._index = next_index
        self.wrapped = self._candidates[next_index]
        logger.warning(
            "ai_model_failover",
            extra={
                "from_model": from_model,
                "to_model": to_model,
                "switch_index": len(self._switches),
                "error_type": type(exc).__name__,
                "status_code": getattr(exc, "status_code", None),
            },
        )
        return next_index

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        index = self._index
        while True:
            try:
                return await self._candidates[index].request(
                    messages,
                    self._settings_for(index, model_settings),
                    model_request_parameters,
                )
            except Exception as exc:
                if index + 1 >= len(self._candidates) or not is_failover_eligible(
                    exc
                ):
                    raise
                self._advance(index, exc)
                index += 1

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """Fail over on stream *establishment* errors only.

        Once a stream is established and yielded to the caller, later chunks
        may already have escaped (deltas, persisted usage), so mid-stream
        failures propagate without switching — streams are never replayed.
        Establishment is proven by reaching the first statement inside the
        inner ``async with``: anything raised before that never yielded, so
        a fresh candidate can take over transparently.
        """
        for index in range(self._index, len(self._candidates)):
            candidate = self._candidates[index]
            established = False
            try:
                async with candidate.request_stream(
                    messages,
                    self._settings_for(index, model_settings),
                    model_request_parameters,
                    run_context,
                ) as stream:
                    established = True
                    self._index = index
                    self.wrapped = candidate
                    yield stream
            except Exception as exc:
                if (
                    established
                    or index + 1 >= len(self._candidates)
                    or not is_failover_eligible(exc)
                ):
                    raise
                self._advance(index, exc)
            else:
                return


@dataclass(frozen=True)
class ChainModel:
    """Agent-facing model plus the primary settings for one config chain."""

    model: Model
    primary_settings: dict[str, object]
    failover: FailoverModel | None
    """Non-None exactly when the chain has fallbacks (even if unused)."""


def build_chain_model(
    configs: list[LLMConfig],
    observer: ModelCallObserver,
    *,
    retry_surface: str,
    model_override: str | None = None,
    max_tokens: int | None = None,
    session_id: str = "",
) -> ChainModel:
    """Build observed candidates plus aligned settings for a config chain.

    Each candidate is observed separately so attempts keep their own usage
    and observer events. The model override and explicit max-tokens apply to
    the primary only: they name a model/cap on the primary's provider and
    may be invalid elsewhere. Fallbacks use their own profile model and
    defaults. A single-config chain returns its lone ``ObservedModel`` with
    no failover wrapper, preserving exact legacy behavior.
    """
    from src.services.agent_runtime.model_factory import (
        agent_model_settings_for_chain,
        create_agent_model,
    )

    if not configs:
        raise ValueError("build_chain_model needs at least one config")
    candidates = [
        ObservedModel(
            create_agent_model(
                config, model=model_override if index == 0 else None
            ),
            observer,
            retry_surface=retry_surface,
        )
        for index, config in enumerate(configs)
    ]
    settings = agent_model_settings_for_chain(
        configs, max_tokens=max_tokens, session_id=session_id
    )
    if len(candidates) == 1:
        return ChainModel(
            model=candidates[0], primary_settings=settings[0], failover=None
        )
    failover = FailoverModel(candidates, candidate_settings=settings)
    return ChainModel(
        model=failover, primary_settings=settings[0], failover=failover
    )
