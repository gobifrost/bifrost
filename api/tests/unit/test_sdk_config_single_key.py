"""SDK config-get uses the repository's narrow keyed read path."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import src.repositories.config as config_repo_module
import shared.sdk_config as sdk_config
from src.core.principal import UserPrincipal
from src.services.authorization.enforce import Caller


@pytest.mark.asyncio
async def test_get_sdk_config_value_does_not_materialize_the_full_config_view(monkeypatch):
    """A hot ``config.get`` must not call ``merged_for_sdk`` / ``HGETALL``."""
    org = uuid4()

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        get_for_sdk = AsyncMock(return_value={"value": "focused", "type": "string"})

        async def merged_for_sdk(self, **kwargs):
            raise AssertionError("single-key SDK read must not load every config")

    caller = Caller(
        UserPrincipal(user_id=uuid4(), email="reader@example.test", organization_id=org),
        None,
    )
    monkeypatch.setattr(config_repo_module, "ConfigRepository", FakeRepo)
    monkeypatch.setattr(sdk_config, "require_permission", lambda *args: None)

    result = await sdk_config.get_sdk_config_value(
        None, key="wanted", org_id=org, caller=caller  # type: ignore[arg-type]
    )

    assert result is not None
    assert result.value == "focused"
    FakeRepo.get_for_sdk.assert_awaited_once_with(
        "wanted", external=False
    )
