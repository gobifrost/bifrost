"""Unit tests for per-request model failover across a profile chain."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from pydantic_ai.exceptions import ModelHTTPError, ModelRetry, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RequestUsage

from src.services.agent_runtime.model_failover import (
    FailoverModel,
    is_failover_eligible,
)


def _params() -> ModelRequestParameters:
    return ModelRequestParameters(function_tools=[])


def _response(text: str = "answer") -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content=text)],
        usage=RequestUsage(input_tokens=10, output_tokens=5),
        model_name="stub",
    )


class ChunkedStream:
    """Minimal async-iterable stream that fails mid-consumption."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self._yielded_first = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._yielded_first:
            self._yielded_first = True
            return "chunk-1"
        raise self._error


class StubModel(Model):
    """Deterministic candidate: raises its error or returns its response."""

    def __init__(
        self,
        name: str,
        *,
        error: Exception | None = None,
        response: ModelResponse | None = None,
        stream_error: Exception | None = None,
        mid_stream_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self._name = name
        self._error = error
        self._response = response or _response(f"from-{name}")
        self._stream_error = stream_error
        self._mid_stream_error = mid_stream_error
        self.calls: list[Any] = []

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def system(self) -> str:
        return "stub"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.calls.append(model_settings)
        if self._error is not None:
            raise self._error
        return self._response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ):
        self.calls.append(model_settings)
        if self._stream_error is not None:
            raise self._stream_error
        if self._mid_stream_error is not None:
            yield ChunkedStream(self._mid_stream_error)
        else:
            yield self._response


def _http_error(status: int) -> ModelHTTPError:
    return ModelHTTPError(status, "stub-model", {"error": "boom"})


# ==================== classifier ====================


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_retryable_statuses_are_eligible(status: int) -> None:
    assert is_failover_eligible(_http_error(status)) is True


@pytest.mark.parametrize("status", [200, 400, 401, 403, 404, 422])
def test_non_retryable_statuses_are_terminal(status: int) -> None:
    assert is_failover_eligible(_http_error(status)) is False


def test_duck_typed_status_code_without_sdk_import() -> None:
    class GatewayError(Exception):
        status_code = 503

    assert is_failover_eligible(GatewayError()) is True

    class BadRequest(Exception):
        status_code = 400

    assert is_failover_eligible(BadRequest()) is False


def test_connection_and_timeout_errors_are_eligible() -> None:
    from anthropic import APIConnectionError as AnthropicConnectionError
    from anthropic import APITimeoutError as AnthropicTimeoutError
    from openai import APIConnectionError as OpenAIConnectionError
    from openai import APITimeoutError as OpenAPITimeoutError

    request = httpx2.Request("POST", "https://example.com")
    assert is_failover_eligible(OpenAIConnectionError(request=request)) is True
    assert is_failover_eligible(OpenAPITimeoutError(request=request)) is True
    assert is_failover_eligible(AnthropicConnectionError(request=request)) is True
    assert is_failover_eligible(AnthropicTimeoutError(request=request)) is True
    assert is_failover_eligible(httpx2.ConnectError("refused")) is True


def test_runtime_and_usage_errors_never_fail_over() -> None:
    assert is_failover_eligible(ValueError("bad input")) is False
    assert is_failover_eligible(UserError("suspended turn")) is False
    assert is_failover_eligible(ModelRetry("try again")) is False
    assert is_failover_eligible(asyncio.CancelledError()) is False


def test_bool_status_code_is_not_a_status() -> None:
    class Weird(Exception):
        status_code = True

    assert is_failover_eligible(Weird()) is False


# ==================== FailoverModel.request ====================


@pytest.mark.asyncio
async def test_primary_success_makes_no_switch() -> None:
    primary = StubModel("primary")
    fallback = StubModel("fallback")
    model = FailoverModel([primary, fallback])
    response = await model.request([], None, _params())
    assert response.text == "from-primary"
    assert model.switches == []
    assert model.fallback_path() == []
    assert model.model_name == "primary"


@pytest.mark.asyncio
async def test_eligible_error_advances_to_fallback() -> None:
    primary = StubModel("primary", error=_http_error(429))
    fallback = StubModel("fallback")
    model = FailoverModel([primary, fallback])
    response = await model.request([], None, _params())
    assert response.text == "from-fallback"
    assert model.switches == [("primary", "fallback")]
    assert model.fallback_path() == ["fallback"]
    assert model.model_name == "fallback"
    assert model.system == "stub"


@pytest.mark.asyncio
async def test_ineligible_error_raises_without_switching() -> None:
    primary = StubModel("primary", error=_http_error(400))
    fallback = StubModel("fallback")
    model = FailoverModel([primary, fallback])
    with pytest.raises(ModelHTTPError):
        await model.request([], None, _params())
    assert fallback.calls == []
    assert model.switches == []


@pytest.mark.asyncio
async def test_exhausted_chain_raises_last_error() -> None:
    primary = StubModel("primary", error=_http_error(503))
    fallback = StubModel("fallback", error=_http_error(500))
    model = FailoverModel([primary, fallback])
    with pytest.raises(ModelHTTPError) as exc_info:
        await model.request([], None, _params())
    assert exc_info.value.status_code == 500
    assert model.fallback_path() == ["fallback"]


@pytest.mark.asyncio
async def test_sticky_candidate_for_later_requests() -> None:
    primary = StubModel("primary", error=_http_error(429))
    fallback = StubModel("fallback")
    model = FailoverModel([primary, fallback])
    await model.request([], None, _params())
    await model.request([], None, _params())
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 2


@pytest.mark.asyncio
async def test_per_candidate_settings_substitution() -> None:
    primary = StubModel("primary", error=_http_error(429))
    fallback = StubModel("fallback")
    model = FailoverModel(
        [primary, fallback],
        candidate_settings=[{"max_tokens": 4000}, {"max_tokens": 8000}],
    )
    await model.request([], {"max_tokens": 1}, _params())
    assert primary.calls == [{"max_tokens": 4000}]
    assert fallback.calls == [{"max_tokens": 8000}]


@pytest.mark.asyncio
async def test_candidate_settings_must_align() -> None:
    with pytest.raises(ValueError):
        FailoverModel([StubModel("only")], candidate_settings=[None, None])
    with pytest.raises(ValueError):
        FailoverModel([])


# ==================== FailoverModel.request_stream ====================


@pytest.mark.asyncio
async def test_stream_establishment_failure_fails_over() -> None:
    primary = StubModel("primary", stream_error=_http_error(503))
    fallback = StubModel("fallback")
    model = FailoverModel([primary, fallback])
    async with model.request_stream([], None, _params()) as stream:
        assert stream.text == "from-fallback"
    assert model.fallback_path() == ["fallback"]


@pytest.mark.asyncio
async def test_mid_stream_error_propagates_without_switch() -> None:
    primary = StubModel("primary", mid_stream_error=RuntimeError("mid-stream"))
    fallback = StubModel("fallback")
    model = FailoverModel([primary, fallback])
    seen = []
    with pytest.raises(RuntimeError, match="mid-stream"):
        async with model.request_stream([], None, _params()) as stream:
            async for chunk in stream:
                seen.append(chunk)
    assert seen == ["chunk-1"]
    assert model.switches == []
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_mid_stream_cancellation_propagates_without_switch() -> None:
    """Cancellation is a BaseException: it must close the stream, never fail over."""
    primary = StubModel("primary")
    fallback = StubModel("fallback")
    model = FailoverModel([primary, fallback])

    async def consume() -> None:
        async with model.request_stream([], None, _params()):
            await asyncio.sleep(60)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()
    done, pending = await asyncio.wait({task})
    assert not pending
    assert task.cancelled()
    assert model.switches == []
    assert fallback.calls == []


# ==================== lifecycle ====================


@pytest.mark.asyncio
async def test_context_enters_all_candidates() -> None:
    entered: list[str] = []

    class Entering(StubModel):
        async def __aenter__(self):
            entered.append(self.model_name)
            return self

    model = FailoverModel([Entering("a"), Entering("b")])
    async with model:
        pass
    assert entered == ["a", "b"]
