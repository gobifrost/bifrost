"""Chat model profile endpoint coverage."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.ai_model_service import AIModelService


async def _seed_profile(
    db_session,
    *,
    name: str,
    enabled_for_chat: bool,
    model: str,
):
    service = AIModelService(db_session)
    connection = await service.create_connection(
        name=f"{name} Connection {uuid4().hex[:8]}",
        provider="openai",
        api_key="test-key",
        endpoint=None,
    )
    return await service.create_profile(
        name=name,
        connection_id=connection.id,
        model=model,
        capabilities=None,
        enabled_for_chat=enabled_for_chat,
    )


@pytest.mark.asyncio
async def test_model_profiles_expose_enabled_profiles_without_provider_model_ids(db_session):
    from src.routers.chat import get_model_profiles

    profile = await _seed_profile(
        db_session,
        name="Everyday",
        enabled_for_chat=True,
        model="provider-private-model",
    )
    await AIModelService(db_session).set_assignment("chat_default", profile.id)

    response = await get_model_profiles(db_session, object())
    payload = response.model_dump(mode="json")

    assert payload["default_profile_id"] == str(profile.id)
    assert payload["profiles"][0]["id"] == str(profile.id)
    assert payload["profiles"][0]["name"] == "Everyday"
    assert payload["profiles"][0]["label"] == "Everyday"
    assert "provider-private-model" not in str(payload)


@pytest.mark.asyncio
async def test_disabled_model_profiles_are_hidden(db_session):
    from src.routers.chat import get_model_profiles

    enabled = await _seed_profile(
        db_session,
        name="Visible",
        enabled_for_chat=True,
        model="visible-model",
    )
    await _seed_profile(
        db_session,
        name="Hidden",
        enabled_for_chat=False,
        model="hidden-model",
    )
    await AIModelService(db_session).set_assignment("chat_default", enabled.id)

    response = await get_model_profiles(db_session, object())
    names = [profile.name for profile in response.profiles]

    assert names == ["Visible"]
    assert response.default_profile_id == enabled.id


@pytest.mark.asyncio
async def test_explicit_disabled_chat_profile_is_rejected(db_session):
    await _seed_profile(
        db_session,
        name="Visible",
        enabled_for_chat=True,
        model="visible-model",
    )
    disabled = await _seed_profile(
        db_session,
        name="Hidden",
        enabled_for_chat=False,
        model="hidden-model",
    )

    with pytest.raises(ValueError, match="Hidden.*not enabled for Chat"):
        await AIModelService(db_session).resolve_chat_profile(disabled.id)


@pytest.mark.asyncio
async def test_omitted_chat_profile_resolves_default_assignment(db_session):
    profile = await _seed_profile(
        db_session,
        name="Default",
        enabled_for_chat=True,
        model="default-model",
    )
    service = AIModelService(db_session)
    await service.set_assignment("chat_default", profile.id)

    resolved_profile, config, _capabilities = await service.resolve_chat_profile()

    assert resolved_profile.id == profile.id
    assert config.model == "default-model"


def _launcher(hostname: str, *, connected: bool = True, **request) -> SimpleNamespace:
    return SimpleNamespace(
        connected=connected, hostname=hostname, request=AsyncMock(**request),
    )


def _control_host(monkeypatch, *bridges: SimpleNamespace):
    from src.worker.remote import host as remote_host

    control = remote_host.RemoteControlHost()
    control.sessions = {uuid4(): SimpleNamespace(bridge=bridge) for bridge in bridges}
    monkeypatch.setattr(remote_host, "_host", control)
    return control


async def _open_chat(db_session) -> None:
    from src.routers.chat import get_model_profiles
    from src.worker.remote import host as remote_host

    await get_model_profiles(db_session, object())
    await asyncio.gather(*tuple(remote_host._prewarms))


@pytest.mark.asyncio
async def test_opening_chat_prewarms_each_launcher_once_per_window(db_session, monkeypatch):
    from src.worker.remote.host import AGENT_PREWARM_INTERVAL_SECONDS

    launcher = _launcher("up", return_value={})
    disconnected = _launcher("off", connected=False)
    control = _control_host(monkeypatch, launcher, disconnected)

    await _open_chat(db_session)
    await _open_chat(db_session)

    launcher.request.assert_awaited_once_with("agent_prewarm", {})
    disconnected.request.assert_not_awaited()

    control.agents_prewarmed_at -= AGENT_PREWARM_INTERVAL_SECONDS
    await _open_chat(db_session)

    assert launcher.request.await_count == 2


@pytest.mark.asyncio
async def test_a_launcher_that_cannot_prewarm_never_fails_the_route(db_session, monkeypatch):
    from src.routers.chat import get_model_profiles
    from src.worker.remote import host as remote_host

    unreachable = _launcher("down", side_effect=ConnectionError("Launcher disconnected"))
    pressured = _launcher("full", side_effect=MemoryError("memory pressure"))
    reachable = _launcher("up", return_value={})
    _control_host(monkeypatch, unreachable, pressured, reachable)
    finished: list[asyncio.Task] = []
    monkeypatch.setattr(remote_host, "_observe_task", finished.append)

    response = await get_model_profiles(db_session, object())
    await asyncio.gather(*tuple(remote_host._prewarms))

    assert response.profiles is not None
    [prewarm] = finished
    assert prewarm.exception() is None
    reachable.request.assert_awaited_once_with("agent_prewarm", {})
