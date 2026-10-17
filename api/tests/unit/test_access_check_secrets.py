"""Secret authorization is recorded without retaining decrypted values."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import src.repositories.config as config_repo_module
from shared import access_checks
import shared.sdk_config as sdk_config
from shared.sdk_integrations import build_oauth_data
from src.core.security import encrypt_secret
from src.core.principal import UserPrincipal
from src.services.authorization.enforce import Caller


@pytest.fixture
def collector():
    token = access_checks.collect_run(uuid4(), uuid4(), uuid4())
    try:
        yield access_checks.current()
    finally:
        access_checks.stop_collecting(token)


async def test_a_config_secret_read_is_noted_without_its_value(collector, monkeypatch) -> None:
    org = uuid4()

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def merged_for_sdk(self, external: bool = False):
            return {"api_key": {"value": encrypt_secret("s3cret"), "type": "secret"}, "plain": {"value": "x"}}

    monkeypatch.setattr(config_repo_module, "ConfigRepository", FakeRepo)

    caller = Caller(UserPrincipal(user_id=uuid4(), email="reader@contoso.example", organization_id=org), None)
    monkeypatch.setattr(sdk_config, "require_permission", lambda *args: None)
    monkeypatch.setattr(sdk_config, "allows_permission", lambda *args: True)
    await sdk_config.get_sdk_config_value(None, key="api_key", org_id=org, caller=caller)  # type: ignore[arg-type]
    await sdk_config.get_sdk_config_value(None, key="plain", org_id=org, caller=caller)  # type: ignore[arg-type]

    assert collector.notes == [
        access_checks.Note("secret", org, {"kind": "config", "name": "api_key", "enforced": True})
    ]


async def test_config_metadata_redacts_a_secret_without_decrypting(monkeypatch) -> None:
    org = uuid4()

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def merged_for_sdk(self, external: bool = False):
            return {"api_key": {"value": encrypt_secret("s3cret"), "type": "secret"}}

    caller = Caller(UserPrincipal(user_id=uuid4(), email="reader@contoso.example", organization_id=org), None)
    monkeypatch.setattr(config_repo_module, "ConfigRepository", FakeRepo)
    monkeypatch.setattr(sdk_config, "require_permission", lambda *args: None)
    monkeypatch.setattr(sdk_config, "allows_permission", lambda *args: False)

    result = await sdk_config.get_sdk_config_value(None, key="api_key", org_id=org, caller=caller)  # type: ignore[arg-type]

    assert result is not None
    assert result.value == "[SECRET]"


async def test_an_integration_call_is_noted_once_with_the_secrets_it_read(collector) -> None:
    org = uuid4()
    provider = SimpleNamespace(
        encrypted_client_secret=encrypt_secret("PLAIN-CLIENT"),
        provider_name="Example",
        client_id="client-id",
        authorization_url=None,
        token_url=None,
        token_url_defaults=None,
        scopes=[],
        oauth_flow_type="authorization_code",
    )
    token = SimpleNamespace(
        encrypted_access_token=encrypt_secret("PLAIN-ACCESS"),
        encrypted_refresh_token=encrypt_secret("PLAIN-REFRESH"),
        expires_at=None,
        organization_id=org,
    )
    from src.core.security import decrypt_secret

    await build_oauth_data(provider, token, None, lambda **_: "", decrypt_secret, can_read_secrets=True)

    assert [(note.target, note.facts) for note in collector.notes] == [
        (org, {"kind": "integration", "name": "Example", "secrets": ["client_secret", "access_token", "refresh_token"]}),
    ]
    assert "PLAIN-" not in repr(collector.notes)
