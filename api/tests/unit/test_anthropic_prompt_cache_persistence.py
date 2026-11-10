"""PostgreSQL isolation and stale-observation fences for learned model capability."""
import asyncio
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.ai_models import AIModelProfile, AIProviderConnection
from src.services.anthropic_prompt_cache import persist_prompt_cache_support
from src.services.llm.base import LLMConfig


@pytest.mark.parametrize("state", ["current", "stale-connection", "changed-model", "stale-profile", "caller-lock"])
async def test_observation_is_profile_scoped_revision_fenced_and_nonblocking(db_session, state):
    connection = AIProviderConnection(name=f"cache-{uuid4().hex}", provider="anthropic", encrypted_api_key="fixture", anthropic_prompt_cache_supported=False)
    db_session.add(connection)
    await db_session.flush()
    a = AIModelProfile(name=f"a-{uuid4().hex}", connection_id=connection.id, model="model-a")
    b = AIModelProfile(name=f"b-{uuid4().hex}", connection_id=connection.id, model="model-b")
    db_session.add_all([a, b])
    await db_session.commit()
    config = LLMConfig(provider="anthropic", model="model-a", api_key="fixture", provider_connection_id=connection.id, model_profile_id=a.id, provider_connection_updated_at=connection.updated_at, model_profile_updated_at=a.updated_at)
    try:
        if state == "stale-connection":
            connection.updated_at = datetime.now(timezone.utc) + timedelta(seconds=1)
            await db_session.commit()
        elif state == "changed-model":
            a.model = "replacement"
            await db_session.commit()
        elif state == "stale-profile":
            a.updated_at = datetime.now(timezone.utc) + timedelta(seconds=1)
            await db_session.commit()
        elif state == "caller-lock":
            await db_session.execute(select(AIModelProfile.id).where(AIModelProfile.id == a.id).with_for_update())

        await asyncio.wait_for(persist_prompt_cache_support(config, False), timeout=1)
        await db_session.rollback()
        await db_session.refresh(a)
        await db_session.refresh(b)
        assert a.anthropic_prompt_cache_supported is (False if state == "current" else None)
        assert b.anthropic_prompt_cache_supported is None
        if state == "current":
            await db_session.commit()
            await persist_prompt_cache_support(config, True)
            await db_session.refresh(a)
            assert a.anthropic_prompt_cache_supported is False, "rejection wins over concurrent success"
    finally:
        await db_session.rollback()
        await db_session.execute(delete(AIModelProfile).where(AIModelProfile.connection_id == config.provider_connection_id))
        await db_session.execute(delete(AIProviderConnection).where(AIProviderConnection.id == config.provider_connection_id))
        await db_session.commit()
