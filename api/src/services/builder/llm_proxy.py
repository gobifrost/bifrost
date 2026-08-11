"""Metered LLM gateway for one job-bound external Builder harness."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cache.redis_client import get_shared_redis
from src.core.database import get_db_context
from src.models.contracts.sandbox_runner import (
    SandboxLLMCompletionRequest,
    SandboxLLMCompletionResponse,
    SandboxLLMMessage,
    SandboxLLMToolCall,
    SandboxLLMToolDefinition,
    SandboxOpenAIChatCompletionRequest,
)
from src.models.orm.agents import Agent, Conversation
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_builder import SolutionBuilderSession, SolutionBuilderTurn
from src.services.ai_usage_service import record_ai_usage
from src.services.llm.base import LLMMessage, ToolCallRequest, ToolDefinition
from src.services.llm.factory import get_llm_client
from src.services.platform_jobs import publish_platform_job_update


logger = logging.getLogger(__name__)
_SSE_KEEPALIVE_SECONDS = 15.0


class BuilderLLMProxyError(RuntimeError):
    pass


class BuilderLLMBudgetExceeded(BuilderLLMProxyError):
    pass


class BuilderLLMCompletionFenced(BuilderLLMProxyError):
    pass


class BuilderLLMUnavailable(BuilderLLMProxyError):
    pass


@dataclass(frozen=True)
class _TurnContext:
    conversation_id: UUID
    organization_id: UUID | None
    user_id: UUID
    model: str
    max_iterations: int
    max_token_budget: int


async def complete_builder_llm(
    db: AsyncSession,
    *,
    job_id: UUID,
    dispatch_attempt: int,
    request: SandboxLLMCompletionRequest,
) -> SandboxLLMCompletionResponse:
    """Run one configured-provider completion without exposing its API key."""
    client = await get_llm_client(db)
    context = await _load_turn_context(db, job_id)
    max_tokens = min(request.max_tokens, client.config.max_tokens)
    reservation = _reservation_tokens(request, max_tokens)
    await _reserve_budget(
        db,
        job_id=job_id,
        dispatch_attempt=dispatch_attempt,
        reservation=reservation,
        max_iterations=context.max_iterations,
        max_token_budget=context.max_token_budget,
    )

    messages, tools = _provider_request_parts(request)

    started = time.monotonic()
    try:
        response = await client.complete(
            messages,
            tools,
            max_tokens=max_tokens,
            model=context.model,
        )
    except Exception as exc:
        await _settle_budget(
            db,
            job_id=job_id,
            dispatch_attempt=dispatch_attempt,
            reservation=reservation,
            input_tokens=0,
            output_tokens=0,
        )
        raise BuilderLLMUnavailable("The configured AI provider request failed") from exc

    duration_ms = int((time.monotonic() - started) * 1000)
    input_tokens = max(0, response.input_tokens or 0)
    output_tokens = max(0, response.output_tokens or 0)
    await _settle_budget(
        db,
        job_id=job_id,
        dispatch_attempt=dispatch_attempt,
        reservation=reservation,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    await _record_usage(
        db,
        provider=client.provider_name,
        model=response.model or context.model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
        api_key=(
            None
            if getattr(client.config, "endpoint", None)
            else client.config.api_key
        ),
        context=context,
    )

    return SandboxLLMCompletionResponse(
        content=response.content,
        tool_calls=(
            [
                SandboxLLMToolCall(
                    id=tool_call.id,
                    name=tool_call.name,
                    arguments=tool_call.arguments,
                )
                for tool_call in response.tool_calls
            ]
            if response.tool_calls
            else None
        ),
        finish_reason=response.finish_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=response.model or context.model,
    )


async def start_builder_openai_stream(
    db: AsyncSession,
    *,
    job_id: UUID,
    dispatch_attempt: int,
    request: SandboxOpenAIChatCompletionRequest,
) -> AsyncIterator[bytes]:
    """Start an OpenAI-compatible SSE stream for a standard coding harness.

    Reservation happens before the response starts so budget/fencing failures
    can still return an ordinary HTTP error. The configured Builder model
    always replaces the model named by the untrusted harness.
    """
    internal_request = _openai_request_to_internal(request)
    client = await get_llm_client(db)
    context = await _load_turn_context(db, job_id)
    max_tokens = min(internal_request.max_tokens, client.config.max_tokens)
    reservation = _reservation_tokens(internal_request, max_tokens)
    await _reserve_budget(
        db,
        job_id=job_id,
        dispatch_attempt=dispatch_attempt,
        reservation=reservation,
        max_iterations=context.max_iterations,
        max_token_budget=context.max_token_budget,
    )
    return _stream_openai_completion(
        db,
        client=client,
        context=context,
        job_id=job_id,
        dispatch_attempt=dispatch_attempt,
        request=internal_request,
        reservation=reservation,
        max_tokens=max_tokens,
    )


async def _stream_openai_completion(
    db: AsyncSession,
    *,
    client: Any,
    context: _TurnContext,
    job_id: UUID,
    dispatch_attempt: int,
    request: SandboxLLMCompletionRequest,
    reservation: int,
    max_tokens: int,
) -> AsyncIterator[bytes]:
    messages, tools = _provider_request_parts(request)
    completion_id = f"chatcmpl-bifrost-{job_id.hex}"
    created = int(time.time())
    input_tokens = 0
    output_tokens = 0
    finish_reason: str | None = None
    settled = False
    tool_call_index = 0
    started = time.monotonic()

    try:
        yield _sse_chunk(
            completion_id,
            created,
            context.model,
            choices=[
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                }
            ],
        )
        provider_stream = client.stream(
            messages,
            tools,
            max_tokens=max_tokens,
            model=context.model,
        )
        async for chunk in _stream_with_keepalives(provider_stream):
            if chunk is None:
                yield b": keepalive\n\n"
                continue
            if chunk.type == "delta" and chunk.content:
                yield _sse_chunk(
                    completion_id,
                    created,
                    context.model,
                    choices=[
                        {
                            "index": 0,
                            "delta": {"content": chunk.content},
                            "finish_reason": None,
                        }
                    ],
                )
            elif chunk.type == "tool_call" and chunk.tool_call is not None:
                yield _sse_chunk(
                    completion_id,
                    created,
                    context.model,
                    choices=[
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": tool_call_index,
                                        "id": chunk.tool_call.id,
                                        "type": "function",
                                        "function": {
                                            "name": chunk.tool_call.name,
                                            "arguments": json.dumps(
                                                chunk.tool_call.arguments,
                                                separators=(",", ":"),
                                            ),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                )
                tool_call_index += 1
            elif chunk.type == "done":
                finish_reason = chunk.finish_reason or "stop"
                input_tokens = max(0, chunk.input_tokens or 0)
                output_tokens = max(0, chunk.output_tokens or 0)
            elif chunk.type == "error":
                raise BuilderLLMUnavailable("The configured AI provider request failed")

        if finish_reason is None:
            raise BuilderLLMUnavailable("The configured AI provider stream ended unexpectedly")

        duration_ms = int((time.monotonic() - started) * 1000)
        await _settle_budget(
            db,
            job_id=job_id,
            dispatch_attempt=dispatch_attempt,
            reservation=reservation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        settled = True
        await _record_usage(
            db,
            provider=client.provider_name,
            model=context.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            api_key=(
                None
                if getattr(client.config, "endpoint", None)
                else client.config.api_key
            ),
            context=context,
        )
        yield _sse_chunk(
            completion_id,
            created,
            context.model,
            choices=[
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": finish_reason,
                }
            ],
        )
        yield _sse_chunk(
            completion_id,
            created,
            context.model,
            choices=[],
            usage={
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        )
        yield b"data: [DONE]\n\n"
    except asyncio.CancelledError:
        # A reverse proxy or harness can cancel an SSE request while the model
        # is still working. The request-owned AsyncSession is being torn down
        # at this point, so release its reservation through an independent,
        # shielded transaction before preserving cancellation semantics.
        settled = True
        try:
            with anyio.CancelScope(shield=True):
                await _settle_cancelled_stream_budget(
                    job_id=job_id,
                    dispatch_attempt=dispatch_attempt,
                    reservation=reservation,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
        except Exception as exc:
            logger.error(
                "Failed to settle cancelled Builder stream budget for job %s (%s)",
                job_id,
                type(exc).__name__,
            )
        raise
    except BuilderLLMUnavailable as exc:
        yield (
            "data: "
            + json.dumps(
                {
                    "error": {
                        "message": str(exc),
                        "type": "server_error",
                        "code": "builder_llm_unavailable",
                    }
                },
                separators=(",", ":"),
            )
            + "\n\n"
        ).encode()
        yield b"data: [DONE]\n\n"
    except Exception as exc:
        # Once StreamingResponse has started, FastAPI can no longer replace it
        # with an ordinary 502. Keep the OpenAI-compatible stream well-formed
        # so a standard harness receives a provider error instead of an opaque
        # network-level "fetch failed". asyncio cancellation is a BaseException
        # on supported Python versions and deliberately continues to propagate.
        logger.error(
            "Builder provider stream failed for job %s (%s)",
            job_id,
            type(exc).__name__,
        )
        yield (
            "data: "
            + json.dumps(
                {
                    "error": {
                        "message": "The configured AI provider request failed",
                        "type": "server_error",
                        "code": "builder_llm_unavailable",
                    }
                },
                separators=(",", ":"),
            )
            + "\n\n"
        ).encode()
        yield b"data: [DONE]\n\n"
    finally:
        if not settled:
            await _settle_budget(
                db,
                job_id=job_id,
                dispatch_attempt=dispatch_attempt,
                reservation=reservation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )


async def _stream_with_keepalives(stream: Any) -> AsyncIterator[Any | None]:
    """Keep the downstream SSE hop active while the provider is thinking."""
    iterator = stream.__aiter__()
    pending = asyncio.create_task(anext(iterator))
    try:
        while True:
            done, _ = await asyncio.wait(
                {pending},
                timeout=_SSE_KEEPALIVE_SECONDS,
            )
            if not done:
                yield None
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            yield chunk
            pending = asyncio.create_task(anext(iterator))
    finally:
        if not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending


async def _settle_cancelled_stream_budget(
    *,
    job_id: UUID,
    dispatch_attempt: int,
    reservation: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    async with get_db_context() as db:
        await _settle_budget(
            db,
            job_id=job_id,
            dispatch_attempt=dispatch_attempt,
            reservation=reservation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def _sse_chunk(
    completion_id: str,
    created: int,
    model: str,
    *,
    choices: list[dict[str, object]],
    usage: dict[str, int] | None = None,
) -> bytes:
    payload: dict[str, object] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": choices,
    }
    if usage is not None:
        payload["usage"] = usage
    return (
        "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"
    ).encode()


def _openai_request_to_internal(
    request: SandboxOpenAIChatCompletionRequest,
) -> SandboxLLMCompletionRequest:
    messages = []
    for message in request.messages:
        role = "system" if message.role == "developer" else message.role
        tool_calls: list[SandboxLLMToolCall] | None = None
        if message.tool_calls:
            tool_calls = []
            for tool_call in message.tool_calls:
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as exc:
                    raise BuilderLLMProxyError(
                        "Harness tool-call arguments must be valid JSON"
                    ) from exc
                if not isinstance(arguments, dict):
                    raise BuilderLLMProxyError(
                        "Harness tool-call arguments must be a JSON object"
                    )
                tool_calls.append(
                    SandboxLLMToolCall(
                        id=tool_call.id,
                        name=tool_call.function.name,
                        arguments=arguments,
                    )
                )
        messages.append(
            SandboxLLMMessage(
                role=role,
                content=_openai_message_content(message.content),
                tool_calls=tool_calls,
                tool_call_id=message.tool_call_id,
                tool_name=message.name,
            )
        )
    tools = (
        [
            SandboxLLMToolDefinition(
                name=tool.function.name,
                description=tool.function.description,
                parameters=tool.function.parameters,
            )
            for tool in request.tools
        ]
        if request.tools
        else None
    )
    max_tokens = (
        request.max_completion_tokens
        or request.max_tokens
        or 16_384
    )
    return SandboxLLMCompletionRequest(
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
    )


def _openai_message_content(content: str | list[dict[str, Any]] | None) -> str | None:
    if content is None or isinstance(content, str):
        return content
    text_parts = [
        part.get("text")
        for part in content
        if part.get("type") in {"text", "input_text", "output_text"}
        and isinstance(part.get("text"), str)
    ]
    if len(text_parts) != len(content):
        raise BuilderLLMProxyError("Builder harness messages support text content only")
    return "\n".join(text_parts)


def _provider_request_parts(
    request: SandboxLLMCompletionRequest,
) -> tuple[list[LLMMessage], list[ToolDefinition] | None]:
    messages = [
        LLMMessage(
            role=message.role,
            content=message.content,
            tool_calls=(
                [
                    ToolCallRequest(
                        id=tool_call.id,
                        name=tool_call.name,
                        arguments=dict(tool_call.arguments),
                    )
                    for tool_call in message.tool_calls
                ]
                if message.tool_calls
                else None
            ),
            tool_call_id=message.tool_call_id,
            tool_name=message.tool_name,
        )
        for message in request.messages
    ]
    tools = (
        [
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=dict(tool.parameters),
            )
            for tool in request.tools
        ]
        if request.tools
        else None
    )
    return messages, tools


async def _record_usage(
    db: AsyncSession,
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    api_key: str | None,
    context: _TurnContext,
) -> None:
    redis_client = await get_shared_redis()
    await record_ai_usage(
        db,
        redis_client,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
        conversation_id=context.conversation_id,
        organization_id=context.organization_id,
        user_id=context.user_id,
        api_key=api_key,
    )
    await db.commit()


async def _load_turn_context(db: AsyncSession, job_id: UUID) -> _TurnContext:
    turn = await db.get(SolutionBuilderTurn, job_id)
    if turn is None:
        raise BuilderLLMCompletionFenced("Builder turn no longer exists")
    session = await db.get(SolutionBuilderSession, turn.session_id)
    if session is None:
        raise BuilderLLMCompletionFenced("Builder session no longer exists")
    conversation = await db.get(Conversation, session.conversation_id)
    agent = (
        await db.get(Agent, conversation.agent_id)
        if conversation is not None and conversation.agent_id is not None
        else None
    )
    if conversation is None or agent is None or not agent.llm_model:
        raise BuilderLLMUnavailable("Builder model is not configured")
    return _TurnContext(
        conversation_id=conversation.id,
        organization_id=agent.organization_id,
        user_id=conversation.user_id,
        model=agent.llm_model,
        max_iterations=agent.max_iterations or 50,
        max_token_budget=agent.max_token_budget or 100_000,
    )


def _reservation_tokens(
    request: SandboxLLMCompletionRequest,
    max_tokens: int,
) -> int:
    # Reserve output capacity before the network call so parallel or
    # compromised callers cannot oversubscribe the turn budget. ASCII-heavy
    # English and source code average roughly four bytes per token; counting
    # every byte as a token rejects normal multi-file tool calls far too early.
    # Keep non-ASCII bytes at one token each as a conservative multilingual
    # bound, then settle the reservation with the provider's actual usage.
    encoded = json.dumps(
        request.model_dump(mode="json", exclude={"max_tokens"}),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    ascii_bytes = sum(byte < 128 for byte in encoded)
    non_ascii_bytes = len(encoded) - ascii_bytes
    estimated_input_tokens = (ascii_bytes + 3) // 4 + non_ascii_bytes
    return estimated_input_tokens + max_tokens


async def _reserve_budget(
    db: AsyncSession,
    *,
    job_id: UUID,
    dispatch_attempt: int,
    reservation: int,
    max_iterations: int,
    max_token_budget: int,
) -> None:
    job = (
        await db.execute(
            select(PlatformJob)
            .where(
                PlatformJob.id == job_id,
                PlatformJob.attempt == dispatch_attempt,
                PlatformJob.status.in_(("running", "waiting")),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        raise BuilderLLMCompletionFenced("Builder LLM capability is no longer active")
    result = dict(job.result or {})
    usage = _usage_dict(result)
    if usage["calls"] >= max_iterations:
        raise BuilderLLMBudgetExceeded("Builder reached its model-call limit")
    committed = usage["input_tokens"] + usage["output_tokens"]
    if committed + usage["reserved_tokens"] + reservation > max_token_budget:
        raise BuilderLLMBudgetExceeded("Builder reached its token budget")
    usage["calls"] += 1
    usage["reserved_tokens"] += reservation
    result["llm_usage"] = usage
    result["llm_limits"] = {
        "max_calls": max_iterations,
        "max_tokens": max_token_budget,
    }
    job.result = result
    _update_job_progress(job, usage, max_token_budget)
    job.revision += 1
    await db.commit()
    await publish_platform_job_update(job)


async def _settle_budget(
    db: AsyncSession,
    *,
    job_id: UUID,
    dispatch_attempt: int,
    reservation: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    job = (
        await db.execute(
            select(PlatformJob)
            .where(
                PlatformJob.id == job_id,
                PlatformJob.attempt == dispatch_attempt,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        raise BuilderLLMCompletionFenced("Builder LLM usage could not be recorded")
    result = dict(job.result or {})
    usage = _usage_dict(result)
    usage["reserved_tokens"] = max(0, usage["reserved_tokens"] - reservation)
    usage["input_tokens"] += input_tokens
    usage["output_tokens"] += output_tokens
    result["llm_usage"] = usage
    job.result = result
    limits = result.get("llm_limits")
    max_token_budget = (
        max(1, int(limits.get("max_tokens", 1)))
        if isinstance(limits, dict)
        else max(1, usage["input_tokens"] + usage["output_tokens"])
    )
    _update_job_progress(job, usage, max_token_budget)
    job.revision += 1
    await db.commit()
    await publish_platform_job_update(job)


def _update_job_progress(
    job: PlatformJob,
    usage: dict[str, int],
    max_token_budget: int,
) -> None:
    committed = usage["input_tokens"] + usage["output_tokens"]
    job.phase = "Building with AI"
    job.progress_current = committed
    job.progress_total = max_token_budget
    job.progress_percent = min(100.0, committed / max_token_budget * 100)


def _usage_dict(result: dict[str, Any]) -> dict[str, int]:
    existing = result.get("llm_usage")
    source = existing if isinstance(existing, dict) else {}
    return {
        "calls": max(0, int(source.get("calls", 0))),
        "input_tokens": max(0, int(source.get("input_tokens", 0))),
        "output_tokens": max(0, int(source.get("output_tokens", 0))),
        "reserved_tokens": max(0, int(source.get("reserved_tokens", 0))),
    }


__all__ = [
    "BuilderLLMBudgetExceeded",
    "BuilderLLMCompletionFenced",
    "BuilderLLMProxyError",
    "BuilderLLMUnavailable",
    "complete_builder_llm",
    "start_builder_openai_stream",
]
