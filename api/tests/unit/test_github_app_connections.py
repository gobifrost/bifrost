"""Provider denial invalidates stored installation authority without a fallback."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from shared import github_app as module
from shared.github_app_auth import GitHubAppError
from shared.github_app_client import AppCredentials


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["installation_missing", "credentials", "permissions", "suspended"])
async def test_revoked_expired_or_suspended_installation_clears_authority(monkeypatch, code):
    generation = uuid4()
    binding = SimpleNamespace(status="active", app_id=123, generation=generation,
        installation_id=4, account_id=44, repositories=[{"id": 10}], last_error=None)
    db = SimpleNamespace(get=AsyncMock(return_value=binding), commit=AsyncMock())

    @asynccontextmanager
    async def database():
        yield db

    provider = SimpleNamespace(installation=AsyncMock(side_effect=GitHubAppError(code, "GitHub denied this request", 409)),
                               repositories=AsyncMock())
    monkeypatch.setattr(module, "get_db_context", database)
    monkeypatch.setattr(module, "credentials", AsyncMock(return_value=AppCredentials(123, "app-generation", "unused")))
    monkeypatch.setattr(module, "provider_client", lambda *args: provider)
    with pytest.raises(GitHubAppError):
        await module.refresh_installation(uuid4())
    assert binding.status == ("suspended" if code == "suspended" else "unavailable")
    assert binding.repositories == []
    assert binding.generation != generation
    provider.repositories.assert_not_awaited()
