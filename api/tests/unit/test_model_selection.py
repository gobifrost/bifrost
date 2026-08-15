"""Dynamic model resolution from system settings.

Tests that summarization and tuning calls honor optional per-purpose model
overrides stored on the LLM provider config, while always reusing the primary
provider + API key.
"""
import base64
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import delete

from src.config import get_settings
from src.models.orm.config import SystemConfig


def _encrypt_api_key(api_key: str) -> str:
    """Mirror LLMConfigService's Fernet encryption so get_llm_client can decrypt."""
    settings = get_settings()
    key_bytes = settings.secret_key.encode()[:32].ljust(32, b"0")
    fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
    return fernet.encrypt(api_key.encode()).decode()


async def _seed_llm_config(
    db_session,
    *,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    summarization_model: str | None = None,
    tuning_model: str | None = None,
    api_key: str = "test-key",
) -> None:
    """Insert a SystemConfig row carrying the LLM config; clear any prior row first."""
    await db_session.execute(
        delete(SystemConfig).where(
            SystemConfig.category == "llm",
            SystemConfig.key == "provider_config",
        )
    )
    value_json: dict = {
        "provider": provider,
        "model": model,
        "encrypted_api_key": _encrypt_api_key(api_key),
        "endpoint": None,
        "max_tokens": 16384,
    }
    if summarization_model is not None:
        value_json["summarization_model"] = summarization_model
    if tuning_model is not None:
        value_json["tuning_model"] = tuning_model

    db_session.add(
        SystemConfig(
            id=uuid4(),
            category="llm",
            key="provider_config",
            value_json=value_json,
            organization_id=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()


@pytest_asyncio.fixture
async def llm_config_cleanup(db_session):
    """Ensure each test starts/ends with no LLM config row."""
    await db_session.execute(
        delete(SystemConfig).where(
            SystemConfig.category == "llm",
            SystemConfig.key == "provider_config",
        )
    )
    await db_session.flush()
    yield
    await db_session.execute(
        delete(SystemConfig).where(
            SystemConfig.category == "llm",
            SystemConfig.key == "provider_config",
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_summarization_falls_back_to_default_model(db_session, llm_config_cleanup):
    from src.services.execution.model_selection import get_summarization_client

    await _seed_llm_config(db_session, model="claude-sonnet-4-6")
    _client, resolved = await get_summarization_client(db_session)
    assert resolved == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_summarization_uses_override_when_set(db_session, llm_config_cleanup):
    from src.services.execution.model_selection import get_summarization_client

    await _seed_llm_config(
        db_session,
        model="claude-sonnet-4-6",
        summarization_model="claude-haiku-4-5",
    )
    _client, resolved = await get_summarization_client(db_session)
    assert resolved == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_tuning_falls_back_to_default_model(db_session, llm_config_cleanup):
    from src.services.execution.model_selection import get_tuning_client

    await _seed_llm_config(db_session, model="claude-sonnet-4-6")
    _client, resolved = await get_tuning_client(db_session)
    assert resolved == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_tuning_uses_override_when_set(db_session, llm_config_cleanup):
    from src.services.execution.model_selection import get_tuning_client

    await _seed_llm_config(
        db_session,
        model="claude-sonnet-4-6",
        tuning_model="claude-opus-4-7",
    )
    _client, resolved = await get_tuning_client(db_session)
    assert resolved == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_provider_always_comes_from_default_config(db_session, llm_config_cleanup):
    """Override only affects model name, not provider or API key."""
    from src.services.execution.model_selection import get_summarization_client
    from src.services.llm.pydantic_client import PydanticAIClient

    await _seed_llm_config(
        db_session,
        provider="anthropic",
        model="claude-sonnet-4-6",
        summarization_model="claude-haiku-4-5",
    )
    client, resolved = await get_summarization_client(db_session)
    assert isinstance(client, PydanticAIClient)
    assert client.provider_name == "anthropic"
    assert client.config.api_key == "test-key"
    assert client.model_name == "claude-sonnet-4-6"
    assert resolved == "claude-haiku-4-5"
