"""
LLM Configuration E2E test fixtures.

Provides fixtures for testing LLM provider configuration endpoints.

Environment variables:
- ANTHROPIC_API_TEST_KEY: Anthropic API key for testing
- OPENAPI_API_TEST_KEY: OpenAI API key for testing
- GENERIC_AI_TEST_KEY: Custom OpenAI-compatible API key
- GENERIC_AI_BASE_URL: Custom endpoint URL (e.g., DeepSeek)
"""

import logging
import os
from collections.abc import AsyncGenerator, Generator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.models.orm.ai_models import AIModelAssignment, AIModelProfile, AIProviderConnection

logger = logging.getLogger(__name__)


def _configure_model_profile(e2e_client, platform_admin, config: dict[str, Any]) -> None:
    """Create a saved provider connection and reusable profile for live-LLM tests."""
    suffix = uuid4().hex[:8]
    connection_response = e2e_client.post(
        "/api/admin/ai/connections",
        json={
            "name": f"E2E {config['provider']} {suffix}",
            "provider": config["provider"],
            "api_key": config["api_key"],
            "endpoint": config.get("endpoint"),
        },
        headers=platform_admin.headers,
    )
    assert connection_response.status_code == 201, connection_response.text
    profile_response = e2e_client.post(
        "/api/admin/ai/profiles",
        json={
            "name": f"E2E Chat {suffix}",
            "connection_id": connection_response.json()["id"],
            "model": config["model"],
            "enabled_for_chat": True,
        },
        headers=platform_admin.headers,
    )
    assert profile_response.status_code == 201, profile_response.text
    profile_id = profile_response.json()["id"]
    for assignment in ("primary", "chat_default"):
        response = e2e_client.put(
            f"/api/admin/ai/assignments/{assignment}",
            json={"profile_id": profile_id},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, response.text


@pytest.fixture(scope="session")
def llm_test_anthropic_key() -> str | None:
    """Get Anthropic test API key from environment."""
    return os.environ.get("ANTHROPIC_API_TEST_KEY")


@pytest.fixture(scope="session")
def llm_test_openai_key() -> str | None:
    """Get OpenAI test API key from environment."""
    return os.environ.get("OPENAPI_API_TEST_KEY")


@pytest.fixture(scope="session")
def llm_test_custom_config() -> dict[str, str] | None:
    """Get custom OpenAI-compatible provider config."""
    key = os.environ.get("GENERIC_AI_TEST_KEY")
    url = os.environ.get("GENERIC_AI_BASE_URL")

    if key and url:
        return {"api_key": key, "endpoint": url}
    return None


@pytest_asyncio.fixture
async def llm_config_cleanup(async_session_factory) -> AsyncGenerator[None, None]:
    """Own test model configuration and restore assignments after each test."""
    async with async_session_factory() as db:
        assignments = [dict(row._mapping) for row in (await db.execute(
            select(AIModelAssignment.assignment_key, AIModelAssignment.profile_id)
        )).all()]
        profile_ids = list((await db.scalars(select(AIModelProfile.id))).all())
        connection_ids = list((await db.scalars(select(AIProviderConnection.id))).all())
        await db.execute(delete(AIModelAssignment))
        await db.commit()
    try:
        yield
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(AIModelAssignment))
            await db.execute(delete(AIModelProfile).where(AIModelProfile.id.not_in(profile_ids)))
            await db.execute(delete(AIProviderConnection).where(AIProviderConnection.id.not_in(connection_ids)))
            db.add_all([AIModelAssignment(**assignment) for assignment in assignments])
            await db.commit()


@pytest.fixture(scope="function")
def llm_anthropic_configured(
    e2e_client,
    platform_admin,
    llm_test_anthropic_key,
    llm_config_cleanup,
) -> Generator[dict[str, Any], None, None]:
    """
    Configure Anthropic as the LLM provider for a test.

    Skips if ANTHROPIC_API_TEST_KEY is not set.

    Yields:
        dict with provider config details
    """
    if not llm_test_anthropic_key:
        pytest.skip("ANTHROPIC_API_TEST_KEY not configured")

    config = {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "api_key": llm_test_anthropic_key,
    }

    _configure_model_profile(e2e_client, platform_admin, config)

    logger.info("Configured Anthropic LLM provider")
    yield config



@pytest.fixture(scope="function")
def llm_openai_configured(
    e2e_client,
    platform_admin,
    llm_test_openai_key,
    llm_config_cleanup,
) -> Generator[dict[str, Any], None, None]:
    """
    Configure OpenAI as the LLM provider for a test.

    Skips if OPENAPI_API_TEST_KEY is not set.

    Yields:
        dict with provider config details
    """
    if not llm_test_openai_key:
        pytest.skip("OPENAPI_API_TEST_KEY not configured")

    config = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": llm_test_openai_key,
    }

    _configure_model_profile(e2e_client, platform_admin, config)

    logger.info("Configured OpenAI LLM provider")
    yield config
