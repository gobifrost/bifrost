import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.sandbox_runner import (
    SandboxLLMCompletionRequest,
    SandboxLLMMessage,
    SandboxOpenAIChatCompletionRequest,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.builder import llm_proxy
from src.services.builder.llm_proxy import BuilderLLMBudgetExceeded
from src.services.llm.base import (
    LLMResponse,
    LLMStreamChunk,
    ToolCallRequest,
)


def _job() -> PlatformJob:
    return PlatformJob(
        id=uuid4(),
        job_type="solution.builder.turn",
        payload_version=1,
        payload={"protected": True},
        requested_by_user_id=str(uuid4()),
        requested_by_email="builder@example.com",
        requested_by_name="Builder",
        title="Building",
        status="waiting",
        phase="Working",
        attempt=1,
        max_attempts=2,
        timeout_seconds=7200,
    )


def test_reservation_estimates_ascii_source_in_token_units() -> None:
    request = SandboxLLMCompletionRequest(
        messages=[SandboxLLMMessage(role="user", content="x" * 4_000)],
        max_tokens=100,
    )

    reservation = llm_proxy._reservation_tokens(request, 100)

    assert 1_100 <= reservation < 2_000


def test_reservation_keeps_non_ascii_bytes_conservative() -> None:
    request = SandboxLLMCompletionRequest(
        messages=[SandboxLLMMessage(role="user", content="界" * 100)],
        max_tokens=100,
    )

    reservation = llm_proxy._reservation_tokens(request, 100)

    assert reservation >= 400


def test_openai_request_conversion_preserves_tools_and_text_parts() -> None:
    request = SandboxOpenAIChatCompletionRequest.model_validate(
        {
            "model": "untrusted-model",
            "messages": [
                {
                    "role": "developer",
                    "content": [{"type": "text", "text": "System rule"}],
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "write",
                                "arguments": '{"path":"app.tsx"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "write",
                    "content": "Done",
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "write",
                        "description": "Write a file",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            "max_completion_tokens": 321,
            "stream": True,
        }
    )

    converted = llm_proxy._openai_request_to_internal(request)

    assert converted.max_tokens == 321
    assert converted.messages[0].role == "system"
    assert converted.messages[0].content == "System rule"
    assert converted.messages[1].tool_calls is not None
    assert converted.messages[1].tool_calls[0].arguments == {"path": "app.tsx"}
    assert converted.messages[2].tool_call_id == "call_1"
    assert converted.tools is not None
    assert converted.tools[0].name == "write"


def test_openai_request_accepts_bounded_coding_harness_tool_descriptions() -> None:
    description = "Use this tool carefully. " * 500
    request = SandboxOpenAIChatCompletionRequest.model_validate(
        {
            "model": "builder",
            "messages": [{"role": "user", "content": "Build it"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "description": description,
                        "parameters": {"type": "object"},
                    },
                }
            ],
        }
    )

    converted = llm_proxy._openai_request_to_internal(request)

    assert converted.tools is not None
    assert converted.tools[0].description == description


def test_openai_request_rejects_unbounded_tool_descriptions() -> None:
    with pytest.raises(ValidationError, match="at most 32768 characters"):
        SandboxOpenAIChatCompletionRequest.model_validate(
            {
                "model": "builder",
                "messages": [{"role": "user", "content": "Build it"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "description": "x" * (32 * 1024 + 1),
                        },
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_completion_reserves_then_settles_job_budget_and_records_usage(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    db_session.add(job)
    await db_session.commit()
    client = SimpleNamespace(
        config=SimpleNamespace(max_tokens=4096, api_key="secret"),
        provider_name="openai",
        complete=AsyncMock(
            return_value=LLMResponse(
                content="Done",
                finish_reason="stop",
                input_tokens=12,
                output_tokens=7,
                model="test-builder-model",
            )
        ),
    )
    monkeypatch.setattr(llm_proxy, "get_llm_client", AsyncMock(return_value=client))
    monkeypatch.setattr(
        llm_proxy,
        "_load_turn_context",
        AsyncMock(
            return_value=llm_proxy._TurnContext(
                conversation_id=uuid4(),
                organization_id=None,
                user_id=uuid4(),
                model="test-builder-model",
                max_iterations=5,
                max_token_budget=10_000,
            )
        ),
    )
    monkeypatch.setattr(llm_proxy, "get_shared_redis", AsyncMock(return_value=object()))
    record = AsyncMock()
    monkeypatch.setattr(llm_proxy, "record_ai_usage", record)
    publish = AsyncMock()
    monkeypatch.setattr(llm_proxy, "publish_platform_job_update", publish)

    response = await llm_proxy.complete_builder_llm(
        db_session,
        job_id=job.id,
        dispatch_attempt=1,
        request=SandboxLLMCompletionRequest(
            messages=[SandboxLLMMessage(role="user", content="Build it")],
            max_tokens=100,
        ),
    )

    assert response.content == "Done"
    await db_session.refresh(job)
    assert job.result == {
        "llm_usage": {
            "calls": 1,
            "input_tokens": 12,
            "output_tokens": 7,
            "reserved_tokens": 0,
        },
        "llm_limits": {"max_calls": 5, "max_tokens": 10_000},
    }
    assert job.phase == "Building with AI"
    assert job.progress_current == 19
    assert job.progress_total == 10_000
    assert job.progress_percent == pytest.approx(0.19)
    assert publish.await_count == 2
    record.assert_awaited_once()
    client.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_rejects_before_provider_call_when_budget_is_exhausted(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    db_session.add(job)
    await db_session.commit()
    client = SimpleNamespace(
        config=SimpleNamespace(max_tokens=4096, api_key="secret"),
        provider_name="openai",
        complete=AsyncMock(),
    )
    monkeypatch.setattr(llm_proxy, "get_llm_client", AsyncMock(return_value=client))
    monkeypatch.setattr(
        llm_proxy,
        "_load_turn_context",
        AsyncMock(
            return_value=llm_proxy._TurnContext(
                conversation_id=uuid4(),
                organization_id=None,
                user_id=uuid4(),
                model="test-builder-model",
                max_iterations=5,
                max_token_budget=10,
            )
        ),
    )

    with pytest.raises(BuilderLLMBudgetExceeded, match="token budget"):
        await llm_proxy.complete_builder_llm(
            db_session,
            job_id=job.id,
            dispatch_attempt=1,
            request=SandboxLLMCompletionRequest(
                messages=[SandboxLLMMessage(role="user", content="Build it")],
                max_tokens=100,
            ),
        )

    client.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_openai_stream_uses_configured_model_and_settles_usage(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    db_session.add(job)
    await db_session.commit()

    async def chunks():
        yield LLMStreamChunk(type="delta", content="Working")
        yield LLMStreamChunk(
            type="tool_call",
            tool_call=ToolCallRequest(
                id="call_1",
                name="write",
                arguments={"path": "app.tsx"},
            ),
        )
        yield LLMStreamChunk(
            type="done",
            finish_reason="tool_calls",
            input_tokens=80,
            output_tokens=12,
        )

    client = SimpleNamespace(
        config=SimpleNamespace(max_tokens=4096, api_key="secret"),
        provider_name="openai",
        stream=MagicMock(side_effect=lambda *_args, **_kwargs: chunks()),
    )
    context = llm_proxy._TurnContext(
        conversation_id=uuid4(),
        organization_id=None,
        user_id=uuid4(),
        model="configured-builder-model",
        max_iterations=5,
        max_token_budget=10_000,
    )
    monkeypatch.setattr(llm_proxy, "get_llm_client", AsyncMock(return_value=client))
    monkeypatch.setattr(llm_proxy, "_load_turn_context", AsyncMock(return_value=context))
    monkeypatch.setattr(llm_proxy, "get_shared_redis", AsyncMock(return_value=object()))
    record = AsyncMock()
    monkeypatch.setattr(llm_proxy, "record_ai_usage", record)
    publish = AsyncMock()
    monkeypatch.setattr(llm_proxy, "publish_platform_job_update", publish)

    stream = await llm_proxy.start_builder_openai_stream(
        db_session,
        job_id=job.id,
        dispatch_attempt=1,
        request=SandboxOpenAIChatCompletionRequest.model_validate(
            {
                "model": "harness-alias",
                "messages": [{"role": "user", "content": "Build it"}],
                "stream": True,
            }
        ),
    )
    body = b"".join([chunk async for chunk in stream])

    assert b'"model":"configured-builder-model"' in body
    assert b'"name":"write"' in body
    assert b'"prompt_tokens":80' in body
    assert body.endswith(b"data: [DONE]\n\n")
    assert client.stream.call_args.kwargs["model"] == "configured-builder-model"
    await db_session.refresh(job)
    assert job.result == {
        "llm_usage": {
            "calls": 1,
            "input_tokens": 80,
            "output_tokens": 12,
            "reserved_tokens": 0,
        },
        "llm_limits": {"max_calls": 5, "max_tokens": 10_000},
    }
    assert job.progress_current == 92
    assert publish.await_count == 2
    record.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_stream_converts_provider_disconnect_to_sse_error(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    db_session.add(job)
    await db_session.commit()

    async def chunks():
        yield LLMStreamChunk(type="delta", content="Working")
        raise RuntimeError("provider connection dropped")

    client = SimpleNamespace(
        config=SimpleNamespace(max_tokens=4096, api_key="secret"),
        provider_name="openai",
        stream=MagicMock(side_effect=lambda *_args, **_kwargs: chunks()),
    )
    context = llm_proxy._TurnContext(
        conversation_id=uuid4(),
        organization_id=None,
        user_id=uuid4(),
        model="configured-builder-model",
        max_iterations=5,
        max_token_budget=10_000,
    )
    monkeypatch.setattr(llm_proxy, "get_llm_client", AsyncMock(return_value=client))
    monkeypatch.setattr(llm_proxy, "_load_turn_context", AsyncMock(return_value=context))
    monkeypatch.setattr(llm_proxy, "get_shared_redis", AsyncMock(return_value=object()))
    record = AsyncMock()
    monkeypatch.setattr(llm_proxy, "record_ai_usage", record)
    publish = AsyncMock()
    monkeypatch.setattr(llm_proxy, "publish_platform_job_update", publish)

    stream = await llm_proxy.start_builder_openai_stream(
        db_session,
        job_id=job.id,
        dispatch_attempt=1,
        request=SandboxOpenAIChatCompletionRequest.model_validate(
            {
                "model": "harness-alias",
                "messages": [{"role": "user", "content": "Build it"}],
                "stream": True,
            }
        ),
    )
    body = b"".join([chunk async for chunk in stream])

    assert b'"code":"builder_llm_unavailable"' in body
    assert b'"message":"The configured AI provider request failed"' in body
    assert b"provider connection dropped" not in body
    assert body.endswith(b"data: [DONE]\n\n")
    await db_session.refresh(job)
    assert job.result == {
        "llm_usage": {
            "calls": 1,
            "input_tokens": 0,
            "output_tokens": 0,
            "reserved_tokens": 0,
        },
        "llm_limits": {"max_calls": 5, "max_tokens": 10_000},
    }
    assert publish.await_count == 2
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_openai_stream_sends_keepalive_while_provider_is_thinking(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    db_session.add(job)
    await db_session.commit()
    release = asyncio.Event()

    async def chunks():
        await release.wait()
        yield LLMStreamChunk(
            type="done",
            finish_reason="stop",
            input_tokens=10,
            output_tokens=2,
        )

    client = SimpleNamespace(
        config=SimpleNamespace(
            max_tokens=4096,
            api_key="compatible-secret",
            endpoint="https://compatible.example.com/v1",
        ),
        provider_name="openai",
        stream=MagicMock(side_effect=lambda *_args, **_kwargs: chunks()),
    )
    context = llm_proxy._TurnContext(
        conversation_id=uuid4(),
        organization_id=None,
        user_id=uuid4(),
        model="configured-builder-model",
        max_iterations=5,
        max_token_budget=10_000,
    )
    monkeypatch.setattr(llm_proxy, "get_llm_client", AsyncMock(return_value=client))
    monkeypatch.setattr(llm_proxy, "_load_turn_context", AsyncMock(return_value=context))
    monkeypatch.setattr(llm_proxy, "get_shared_redis", AsyncMock(return_value=object()))
    monkeypatch.setattr(llm_proxy, "_SSE_KEEPALIVE_SECONDS", 0.001)
    record = AsyncMock()
    monkeypatch.setattr(llm_proxy, "record_ai_usage", record)
    monkeypatch.setattr(
        llm_proxy,
        "publish_platform_job_update",
        AsyncMock(),
    )

    stream = await llm_proxy.start_builder_openai_stream(
        db_session,
        job_id=job.id,
        dispatch_attempt=1,
        request=SandboxOpenAIChatCompletionRequest.model_validate(
            {
                "model": "harness-alias",
                "messages": [{"role": "user", "content": "Build it"}],
                "stream": True,
            }
        ),
    )
    iterator = stream.__aiter__()
    assert b'"role":"assistant"' in await anext(iterator)
    assert await anext(iterator) == b": keepalive\n\n"
    release.set()
    body = b"".join([chunk async for chunk in iterator])

    assert body.endswith(b"data: [DONE]\n\n")
    assert record.await_args.kwargs["api_key"] is None


@pytest.mark.asyncio
async def test_openai_stream_cancellation_uses_independent_budget_cleanup(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    db_session.add(job)
    await db_session.commit()
    never = asyncio.Event()

    async def chunks():
        await never.wait()
        yield LLMStreamChunk(type="done", finish_reason="stop")

    client = SimpleNamespace(
        config=SimpleNamespace(max_tokens=4096, api_key="secret", endpoint=None),
        provider_name="openai",
        stream=MagicMock(side_effect=lambda *_args, **_kwargs: chunks()),
    )
    context = llm_proxy._TurnContext(
        conversation_id=uuid4(),
        organization_id=None,
        user_id=uuid4(),
        model="configured-builder-model",
        max_iterations=5,
        max_token_budget=10_000,
    )
    monkeypatch.setattr(llm_proxy, "get_llm_client", AsyncMock(return_value=client))
    monkeypatch.setattr(llm_proxy, "_load_turn_context", AsyncMock(return_value=context))
    cleanup = AsyncMock()
    monkeypatch.setattr(llm_proxy, "_settle_cancelled_stream_budget", cleanup)
    monkeypatch.setattr(
        llm_proxy,
        "publish_platform_job_update",
        AsyncMock(),
    )

    stream = await llm_proxy.start_builder_openai_stream(
        db_session,
        job_id=job.id,
        dispatch_attempt=1,
        request=SandboxOpenAIChatCompletionRequest.model_validate(
            {
                "model": "harness-alias",
                "messages": [{"role": "user", "content": "Build it"}],
                "stream": True,
            }
        ),
    )
    iterator = stream.__aiter__()
    await anext(iterator)
    blocked = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)
    blocked.cancel()

    with pytest.raises(asyncio.CancelledError):
        await blocked
    cleanup.assert_awaited_once()
