from uuid import uuid4

import pytest

from src.models.orm.agents import Agent
from src.services.ai_model_service import (
    AIModelService,
    OPENROUTER_DEFAULT_ENDPOINT,
    PROVIDER_DEFAULT_ENDPOINTS,
)


async def _connection(service: AIModelService):
    return await service.create_connection(
        name=f"Default {uuid4().hex[:8]}",
        provider="openrouter",
        api_key="sk-test",
        endpoint=None,
    )


async def _profile(service: AIModelService, *, enabled_for_chat: bool = False):
    if not await service.list_profiles():
        seed_connection = await _connection(service)
        await service.create_profile(
            name=f"Initial {uuid4().hex[:8]}",
            connection_id=seed_connection.id,
            model="openai/gpt-4o-mini",
            capabilities=None,
            enabled_for_chat=True,
        )
    connection = await _connection(service)
    return await service.create_profile(
        name=f"Balanced {uuid4().hex[:8]}",
        connection_id=connection.id,
        model="openai/gpt-4o-mini",
        capabilities=None,
        enabled_for_chat=enabled_for_chat,
    )


@pytest.mark.asyncio
async def test_first_profile_bootstraps_every_assignment(db_session):
    service = AIModelService(db_session)
    connection = await _connection(service)

    profile = await service.create_profile(
        name="First Profile",
        connection_id=connection.id,
        model="openai/gpt-4o-mini",
        capabilities=None,
        enabled_for_chat=False,
    )
    assignments = await service.list_assignments()

    assert profile.enabled_for_chat is True
    assert {assignment.assignment_key for assignment in assignments} == {
        "primary",
        "summarization",
        "tuning",
        "image_generation",
        "video_generation",
        "chat_default",
    }
    assert {assignment.profile_id for assignment in assignments} == {profile.id}


@pytest.mark.asyncio
async def test_create_connection_requires_key_and_defaults_openrouter_endpoint(
    db_session,
):
    service = AIModelService(db_session)

    with pytest.raises(ValueError, match="API key is required"):
        await service.create_connection(
            name="Default",
            provider="openai",
            api_key=" ",
            endpoint=None,
        )

    connection = await service.create_connection(
        name="Default",
        provider="openrouter",
        api_key="sk-test",
        endpoint=None,
    )

    assert connection.endpoint == OPENROUTER_DEFAULT_ENDPOINT
    assert connection.encrypted_api_key != "sk-test"
    assert service.decrypt_api_key(connection.encrypted_api_key) == "sk-test"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openrouter", "google", "anthropic"])
async def test_create_connection_defaults_builtin_provider_endpoints(
    db_session, provider
):
    service = AIModelService(db_session)

    connection = await service.create_connection(
        name=f"{provider} {uuid4().hex[:8]}",
        provider=provider,
        api_key="sk-test",
        endpoint=None,
    )

    assert connection.endpoint == PROVIDER_DEFAULT_ENDPOINTS[provider]


@pytest.mark.asyncio
async def test_openai_compatible_connection_requires_endpoint(db_session):
    service = AIModelService(db_session)

    with pytest.raises(ValueError, match="Endpoint is required"):
        await service.create_connection(
            name="Compatible",
            provider="openai_compatible",
            api_key="sk-test",
            endpoint=None,
        )


@pytest.mark.asyncio
async def test_connection_update_preserves_stored_key_when_omitted(db_session):
    service = AIModelService(db_session)
    connection = await _connection(service)
    encrypted_before = connection.encrypted_api_key

    updated = await service.update_connection(
        connection.id,
        name="Renamed Default",
        provider="openai_compatible",
        endpoint="https://models.example.test/v1",
    )

    assert updated.encrypted_api_key == encrypted_before
    assert updated.endpoint == "https://models.example.test/v1"
    assert service.decrypt_api_key(updated.encrypted_api_key) == "sk-test"


@pytest.mark.asyncio
async def test_case_insensitive_unique_names(db_session):
    service = AIModelService(db_session)
    await service.create_connection(
        name="Default", provider="openai", api_key="sk-test", endpoint=None
    )

    with pytest.raises(ValueError, match="Provider connection name already exists"):
        await service.create_connection(
            name="default", provider="openai", api_key="sk-test", endpoint=None
        )


@pytest.mark.asyncio
async def test_chat_default_requires_chat_enabled_profile(db_session):
    service = AIModelService(db_session)
    profile = await _profile(service, enabled_for_chat=False)

    with pytest.raises(ValueError, match="chat-enabled"):
        await service.set_assignment("chat_default", profile.id)

    await service.update_profile(profile.id, enabled_for_chat=True)
    assignment = await service.set_assignment("chat_default", profile.id)

    assert assignment.assignment_key == "chat_default"
    assert assignment.profile_id == profile.id


@pytest.mark.asyncio
async def test_profile_delete_is_blocked_while_assigned(db_session):
    service = AIModelService(db_session)
    profile = await _profile(service)
    await service.set_assignment("primary", profile.id)

    with pytest.raises(ValueError, match="used by assignments"):
        await service.delete_profile(profile.id)

    replacement = await _profile(service)
    reassigned = await service.set_assignment("primary", replacement.id)
    assert reassigned.profile.id == replacement.id
    await service.delete_profile(profile.id)


@pytest.mark.asyncio
async def test_merge_profiles_reassigns_agents_assignments_and_chat(db_session):
    service = AIModelService(db_session)
    target = await _profile(service, enabled_for_chat=False)
    source = await _profile(service, enabled_for_chat=True)
    second_source = await _profile(service, enabled_for_chat=False)
    await service.set_assignment("primary", source.id)
    await service.set_assignment("chat_default", source.id)
    await service.set_assignment("summarization", second_source.id)
    agent = Agent(
        name="Merged Profile Agent",
        system_prompt="Use the selected profile.",
        created_by="test",
        llm_profile=source,
    )
    db_session.add(agent)
    await db_session.flush()

    result = await service.merge_profiles(
        profile_ids=[target.id, source.id, second_source.id],
        target_profile_id=target.id,
    )

    assert result.profile.id == target.id
    assert result.profile.enabled_for_chat is True
    assert set(result.merged_profile_ids) == {source.id, second_source.id}
    assert result.reassigned_agent_count == 1
    assert set(result.reassigned_assignment_keys) == {
        "primary",
        "summarization",
        "chat_default",
    }
    assert agent.llm_profile_id == target.id
    assert {
        assignment.profile_id
        for assignment in await service.list_assignments()
        if assignment.assignment_key in {"primary", "summarization", "chat_default"}
    } == {target.id}
    assert {profile.id for profile in await service.list_profiles()}.isdisjoint(
        {source.id, second_source.id}
    )


@pytest.mark.asyncio
async def test_merge_profiles_requires_target_in_unique_selection(db_session):
    service = AIModelService(db_session)
    target = await _profile(service)
    source = await _profile(service)

    with pytest.raises(ValueError, match="duplicates"):
        await service.merge_profiles(
            profile_ids=[source.id, source.id],
            target_profile_id=source.id,
        )

    with pytest.raises(ValueError, match="included"):
        await service.merge_profiles(
            profile_ids=[source.id, target.id],
            target_profile_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_resolve_config_defaults_to_platform_default_assignment(db_session):
    service = AIModelService(db_session)
    connection = await service.create_connection(
        name="OpenAI Compatible",
        provider="openai_compatible",
        api_key="compatible-key",
        endpoint="https://llm.example.test/v1",
    )
    profile = await service.create_profile(
        name="Primary",
        connection_id=connection.id,
        model="custom/model",
        capabilities=None,
        enabled_for_chat=False,
    )
    await service.set_assignment("primary", profile.id)

    config = await service.resolve_config()

    assert config.provider == "openai"
    assert config.endpoint == "https://llm.example.test/v1"
    assert config.api_key == "compatible-key"
    assert config.model == "custom/model"


@pytest.mark.asyncio
async def test_resolve_config_from_explicit_profile_uuid(db_session):
    service = AIModelService(db_session)
    profile = await _profile(service)

    config = await service.resolve_config(profile_id=profile.id)

    assert config.provider == "openai"
    assert config.endpoint == "https://openrouter.ai/api/v1"
    assert config.model == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_resolve_config_from_case_insensitive_profile_name(db_session):
    service = AIModelService(db_session)
    profile = await _profile(service)

    config = await service.resolve_config(profile_name=profile.name.swapcase())

    assert config.provider == "openai"
    assert config.endpoint == "https://openrouter.ai/api/v1"
    assert config.model == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_resolve_config_rejects_unknown_profile_name(db_session):
    service = AIModelService(db_session)

    with pytest.raises(ValueError, match="Model profile 'Missing' was not found"):
        await service.resolve_config(profile_name="Missing")


@pytest.mark.asyncio
async def test_embedding_config_resolves_selected_provider_connection(db_session):
    service = AIModelService(db_session)
    connection = await service.create_connection(
        name="Embedding Provider",
        provider="openai_compatible",
        api_key="embedding-key",
        endpoint="https://embeddings.example.test/v1",
    )

    await service.set_embedding_config(
        connection_id=connection.id,
        model="custom/embedding",
        dimensions=768,
    )
    config = await service.resolve_embedding_config()

    assert config.api_key == "embedding-key"
    assert config.model == "custom/embedding"
    assert config.dimensions == 768
    assert config.endpoint == "https://embeddings.example.test/v1"


@pytest.mark.asyncio
async def test_connection_delete_is_blocked_while_used_for_embeddings(db_session):
    service = AIModelService(db_session)
    connection = await service.create_connection(
        name="Embedding Provider",
        provider="openai",
        api_key="embedding-key",
        endpoint=None,
    )
    await service.set_embedding_config(
        connection_id=connection.id,
        model="text-embedding-3-small",
        dimensions=1536,
    )

    with pytest.raises(ValueError, match="embedding configuration"):
        await service.delete_connection(connection.id)

    await service.delete_embedding_config()
    await service.delete_connection(connection.id)
