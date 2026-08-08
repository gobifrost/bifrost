from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.sandbox_runner import (
    SandboxLLMCompletionRequest,
    SandboxLLMMessage,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.builder import llm_proxy
from src.services.builder.llm_proxy import BuilderLLMBudgetExceeded
from src.services.llm.base import LLMResponse


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
        }
    }
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
