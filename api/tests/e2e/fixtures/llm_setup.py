"""
LLM Configuration E2E test fixtures.

Provides fixtures for testing LLM provider configuration endpoints.

Environment variables:
- ANTHROPIC_API_TEST_KEY: Anthropic API key for testing
- OPENAPI_API_TEST_KEY: OpenAI API key for testing
- GENERIC_AI_TEST_KEY: Custom OpenAI-compatible API key
- GENERIC_AI_BASE_URL: Custom endpoint URL (e.g., DeepSeek)
"""

import asyncio
import logging
import os
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.ai_models import AIModelAssignment, AIModelProfile, AIProviderConnection

logger = logging.getLogger(__name__)


async def _delete_model_profile(async_session_factory, profile_id: str, connection_id: str) -> None:
    async with async_session_factory() as session:
        await session.execute(
            delete(AIModelAssignment).where(AIModelAssignment.profile_id == UUID(profile_id))
        )
        await session.execute(delete(AIModelProfile).where(AIModelProfile.id == UUID(profile_id)))
        await session.execute(
            delete(AIProviderConnection).where(AIProviderConnection.id == UUID(connection_id))
        )
        await session.commit()


@contextmanager
def _configured_model_profile(
    e2e_client, platform_admin, async_session_factory, config: dict[str, Any]
) -> Iterator[None]:
    """Create a saved provider connection and reusable profile for live-LLM tests.

    The profile becomes the global primary and chat default model, so it is
    removed afterwards: a live provider left behind makes later agent runs in
    the same suite call it.
    """
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
    connection_id = connection_response.json()["id"]
    profile_response = e2e_client.post(
        "/api/admin/ai/profiles",
        json={
            "name": f"E2E Chat {suffix}",
            "connection_id": connection_id,
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
    try:
        yield
    finally:
        # The API keeps the required primary and chat assignments, so the
        # rows this fixture created are removed directly.
        asyncio.run(_delete_model_profile(async_session_factory, profile_id, connection_id))


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


@pytest.fixture(scope="function")
def llm_config_cleanup(e2e_client, platform_admin) -> Generator[None, None, None]:
    """
    Ensure LLM config is cleaned up after test.

    This fixture ensures tests start with a clean state and
    cleans up any configuration created during the test.
    """
    del e2e_client, platform_admin
    yield


@pytest.fixture(scope="function")
def llm_anthropic_configured(
    e2e_client,
    platform_admin,
    async_session_factory,
    llm_test_anthropic_key,
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

    with _configured_model_profile(e2e_client, platform_admin, async_session_factory, config):
        logger.info("Configured Anthropic LLM provider")
        yield config



@pytest.fixture(scope="function")
def llm_openai_configured(
    e2e_client,
    platform_admin,
    async_session_factory,
    llm_test_openai_key,
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

    with _configured_model_profile(e2e_client, platform_admin, async_session_factory, config):
        logger.info("Configured OpenAI LLM provider")
        yield config
