"""Metered LLM gateway for one job-bound external Builder harness."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cache.redis_client import get_shared_redis
from src.models.contracts.sandbox_runner import (
    SandboxLLMCompletionRequest,
    SandboxLLMCompletionResponse,
    SandboxLLMToolCall,
)
from src.models.orm.agents import Agent, Conversation
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_builder import SolutionBuilderSession, SolutionBuilderTurn
from src.services.ai_usage_service import record_ai_usage
from src.services.llm.base import LLMMessage, ToolCallRequest, ToolDefinition
from src.services.llm.factory import get_llm_client


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
    redis_client = await get_shared_redis()
    await record_ai_usage(
        db,
        redis_client,
        provider=client.provider_name,
        model=response.model or context.model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
        conversation_id=context.conversation_id,
        organization_id=context.organization_id,
        user_id=context.user_id,
        api_key=client.config.api_key,
    )
    await db.commit()

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
    # UTF-8 bytes are a conservative upper bound for tokenizer units across
    # configured providers. Reserve output capacity before the network call so
    # parallel or compromised callers cannot oversubscribe the turn budget.
    encoded = json.dumps(
        request.model_dump(mode="json", exclude={"max_tokens"}),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return len(encoded) + max_tokens


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
    job.result = result
    job.revision += 1
    await db.commit()


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
    job.revision += 1
    await db.commit()


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
]
