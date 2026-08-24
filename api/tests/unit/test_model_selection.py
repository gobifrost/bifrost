"""Dynamic model resolution from reusable AI model assignments."""

from uuid import uuid4

import pytest

from src.services.ai_model_service import AIModelService


async def _seed_profile_assignment(
    db_session,
    *,
    assignment_key: str,
    provider: str = "anthropic",
    endpoint: str | None = None,
    model: str = "claude-sonnet-4-6",
    api_key: str = "test-key",
):
    service = AIModelService(db_session)
    connection = await service.create_connection(
        name=f"Connection {uuid4().hex[:8]}",
        provider=provider,
        api_key=api_key,
        endpoint=endpoint,
    )
    profile = await service.create_profile(
        name=f"Profile {uuid4().hex[:8]}",
        connection_id=connection.id,
        model=model,
        capabilities=None,
        enabled_for_chat=assignment_key == "chat_default",
    )
    await service.set_assignment(assignment_key, profile.id)
    return profile


@pytest.mark.asyncio
async def test_summarization_uses_summarization_assignment(db_session):
    from src.services.execution.model_selection import get_summarization_client
    from src.services.llm.pydantic_client import PydanticAIClient

    await _seed_profile_assignment(
        db_session,
        assignment_key="summarization",
        provider="anthropic",
        model="claude-haiku-4-5",
    )

    client, resolved = await get_summarization_client(db_session)

    assert isinstance(client, PydanticAIClient)
    assert client.provider_name == "anthropic"
    assert client.config.api_key == "test-key"
    assert client.config.model == "claude-haiku-4-5"
    assert resolved == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_tuning_uses_tuning_assignment(db_session):
    from src.services.execution.model_selection import get_tuning_client

    await _seed_profile_assignment(
        db_session,
        assignment_key="tuning",
        provider="google",
        model="gemini-2.5-flash",
        api_key="google-key",
    )

    client, resolved = await get_tuning_client(db_session)

    assert client.provider_name == "google"
    assert client.config.api_key == "google-key"
    assert resolved == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_openrouter_assignment_runs_as_openai_with_endpoint(db_session):
    from src.services.execution.model_selection import get_summarization_client

    await _seed_profile_assignment(
        db_session,
        assignment_key="summarization",
        provider="openrouter",
        model="openai/gpt-4o-mini",
        api_key="openrouter-key",
    )

    client, resolved = await get_summarization_client(db_session)

    assert client.provider_name == "openrouter"
    assert client.config.provider == "openai"
    assert client.config.endpoint == "https://openrouter.ai/api/v1"
    assert client.config.api_key == "openrouter-key"
    assert resolved == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_missing_summarization_assignment_reports_clear_error(db_session):
    from src.services.execution.model_selection import get_summarization_client

    with pytest.raises(RuntimeError, match="summarization.*not configured"):
        await get_summarization_client(db_session)
