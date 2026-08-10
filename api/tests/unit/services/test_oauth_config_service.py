from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.oauth_config import OAuthLoginPreference
from src.services.oauth_config_service import (
    OAuthConfigService,
    OAuthLoginPreferenceError,
    OAuthProviderConfig,
    OAuthProviderPreferenceConflict,
)


def make_db() -> MagicMock:
    db = MagicMock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    return db


def test_enabled_login_preference_requires_provider() -> None:
    with pytest.raises(ValidationError, match="default_sso_provider is required"):
        OAuthLoginPreference(auto_redirect_to_sso=True)


@pytest.mark.asyncio
async def test_login_preference_defaults_to_disabled() -> None:
    db = make_db()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result

    preference = await OAuthConfigService(db).get_login_preference()

    assert preference == OAuthLoginPreference()


@pytest.mark.asyncio
async def test_login_preference_requires_configured_provider() -> None:
    service = OAuthConfigService(make_db())
    service.get_provider_config = AsyncMock(return_value=None)

    with pytest.raises(OAuthLoginPreferenceError, match="must be configured"):
        await service.set_login_preference(
            OAuthLoginPreference(
                auto_redirect_to_sso=True,
                default_sso_provider="microsoft",
            )
        )


@pytest.mark.asyncio
async def test_login_preference_is_persisted_for_configured_provider() -> None:
    db = make_db()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    service = OAuthConfigService(db)
    service.get_provider_config = AsyncMock(
        return_value=OAuthProviderConfig(
            provider="microsoft",
            client_id="client-id",
            client_secret="client-secret",
        )
    )
    preference = OAuthLoginPreference(
        auto_redirect_to_sso=True,
        default_sso_provider="microsoft",
    )

    assert await service.set_login_preference(preference) == preference
    added = db.add.call_args.args[0]
    assert added.value_json == preference.model_dump(mode="json")
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_active_preferred_provider_cannot_be_deleted() -> None:
    service = OAuthConfigService(make_db())
    service.get_login_preference = AsyncMock(
        return_value=OAuthLoginPreference(
            auto_redirect_to_sso=True,
            default_sso_provider="microsoft",
        )
    )
    service._delete_config_keys = AsyncMock()

    with pytest.raises(
        OAuthProviderPreferenceConflict,
        match="Disable preferred SSO redirect",
    ):
        await service.delete_provider_config("microsoft")

    service._delete_config_keys.assert_not_awaited()
