from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main import create_default_user


def _db_context(db: AsyncMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=db)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


@pytest.mark.asyncio
async def test_existing_debug_user_credentials_follow_config() -> None:
    settings = SimpleNamespace(
        default_user_email="dev@gobifrost.com",
        default_user_password="unique-public-password",
        debug=True,
    )
    existing = SimpleNamespace(hashed_password="old-hash", mfa_enabled=True)
    repository = MagicMock()
    repository.get_by_email = AsyncMock(return_value=existing)
    db = AsyncMock()

    with (
        patch("src.main.get_settings", return_value=settings),
        patch("src.core.database.get_db_context", return_value=_db_context(db)),
        patch("src.core.security.get_password_hash", return_value="new-hash"),
        patch("src.repositories.users.UserRepository", return_value=repository),
        patch("src.services.user_provisioning.ensure_user_provisioned") as provision,
    ):
        await create_default_user()

    assert existing.hashed_password == "new-hash"
    assert existing.mfa_enabled is False
    db.commit.assert_awaited_once()
    provision.assert_not_called()


@pytest.mark.asyncio
async def test_existing_non_debug_user_credentials_are_not_changed() -> None:
    settings = SimpleNamespace(
        default_user_email="admin@example.com",
        default_user_password="configured-password",
        debug=False,
    )
    existing = SimpleNamespace(hashed_password="existing-hash", mfa_enabled=True)
    repository = MagicMock()
    repository.get_by_email = AsyncMock(return_value=existing)
    db = AsyncMock()

    with (
        patch("src.main.get_settings", return_value=settings),
        patch("src.core.database.get_db_context", return_value=_db_context(db)),
        patch("src.core.security.get_password_hash") as password_hash,
        patch("src.repositories.users.UserRepository", return_value=repository),
        patch("src.services.user_provisioning.ensure_user_provisioned") as provision,
    ):
        await create_default_user()

    assert existing.hashed_password == "existing-hash"
    assert existing.mfa_enabled is True
    db.commit.assert_not_awaited()
    password_hash.assert_not_called()
    provision.assert_not_called()
