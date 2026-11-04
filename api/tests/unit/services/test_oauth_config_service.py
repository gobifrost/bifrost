from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.oauth_config import OAuthLoginPreference
from src.repositories.users import UserRepository
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


def test_old_preference_rows_decode_with_login_enabled() -> None:
    """Rows written before the SSO-only flags default both to True."""
    assert OAuthLoginPreference.model_validate({}).password_login_enabled is True
    assert OAuthLoginPreference.model_validate({}).passkey_login_enabled is True
    legacy = OAuthLoginPreference.model_validate(
        {"auto_redirect_to_sso": False, "default_sso_provider": None}
    )
    assert legacy.password_login_enabled is True
    assert legacy.passkey_login_enabled is True


def _service_with_preference(**kwargs) -> OAuthConfigService:
    service = OAuthConfigService(make_db())
    service.get_login_preference = AsyncMock(  # type: ignore[method-assign]
        return_value=OAuthLoginPreference(**kwargs)
    )
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "password_enabled,passkey_enabled,is_admin,has_users,expect_password,expect_passkey",
    [
        (True, True, False, True, True, True),  # defaults: open
        (False, True, False, True, False, True),  # password SSO-only
        (True, False, False, True, True, False),  # passkey SSO-only
        (False, False, False, True, False, False),  # full SSO-only
        (False, False, True, True, True, True),  # admin exempt
        (False, True, True, True, True, True),  # admin exempt
        (False, False, False, False, True, True),  # bootstrap bypass
    ],
)
async def test_login_method_allowed_truth_table(
    password_enabled: bool,
    passkey_enabled: bool,
    is_admin: bool,
    has_users: bool,
    expect_password: bool,
    expect_passkey: bool,
) -> None:
    service = _service_with_preference(
        password_login_enabled=password_enabled,
        passkey_login_enabled=passkey_enabled,
    )
    with patch.object(
        UserRepository,
        "has_any_users",
        new=AsyncMock(return_value=has_users),
    ):
        assert await service.is_password_allowed_for(SimpleNamespace(id=uuid4(), is_superuser=is_admin, organization_id=None)) is expect_password
        assert await service.is_passkey_allowed_for(SimpleNamespace(id=uuid4(), is_superuser=is_admin, organization_id=None)) is expect_passkey


@pytest.mark.asyncio
async def test_sso_only_preference_requires_provider() -> None:
    service = OAuthConfigService(make_db())
    service.get_provider_config = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(OAuthLoginPreferenceError, match="must be configured"):
        await service.set_login_preference(
            OAuthLoginPreference(password_login_enabled=False)
        )
    with pytest.raises(OAuthLoginPreferenceError, match="must be configured"):
        await service.set_login_preference(
            OAuthLoginPreference(passkey_login_enabled=False)
        )


@pytest.mark.asyncio
async def test_sso_only_preference_persisted_with_provider() -> None:
    db = make_db()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    service = OAuthConfigService(db)
    service.get_provider_config = AsyncMock(  # type: ignore[method-assign]
        return_value=OAuthProviderConfig(
            provider="microsoft",
            client_id="client-id",
            client_secret="client-secret",
        )
    )
    preference = OAuthLoginPreference(
        password_login_enabled=False,
        passkey_login_enabled=False,
    )

    assert await service.set_login_preference(preference) == preference
    added = db.add.call_args.args[0]
    assert added.value_json == preference.model_dump(mode="json")
    db.flush.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_enforced_last_provider_cannot_be_deleted() -> None:
    service = _service_with_preference(password_login_enabled=False)
    service.get_available_providers = AsyncMock(return_value=["microsoft"])  # type: ignore[method-assign]
    service._delete_config_keys = AsyncMock()

    with pytest.raises(
        OAuthProviderPreferenceConflict,
        match="last SSO provider",
    ):
        await service.delete_provider_config("microsoft")

    service._delete_config_keys.assert_not_awaited()


@pytest.mark.asyncio
async def test_enforced_non_last_provider_can_be_deleted() -> None:
    service = _service_with_preference(passkey_login_enabled=False)
    service.get_available_providers = AsyncMock(return_value=["microsoft", "google"])  # type: ignore[method-assign]
    service._delete_config_keys = AsyncMock()

    assert await service.delete_provider_config("microsoft") is True
    service._delete_config_keys.assert_awaited_once()


@pytest.mark.asyncio
async def test_unenforced_last_provider_can_be_deleted() -> None:
    service = _service_with_preference()
    service.get_available_providers = AsyncMock(return_value=["microsoft"])  # type: ignore[method-assign]
    service._delete_config_keys = AsyncMock()

    assert await service.delete_provider_config("microsoft") is True
    service._delete_config_keys.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleting_default_provider_preserves_enforcement() -> None:
    """Resetting the preferred redirect must not silently reopen login."""
    service = _service_with_preference(
        password_login_enabled=False,
        passkey_login_enabled=False,
        default_sso_provider="microsoft",
    )
    service.get_available_providers = AsyncMock(return_value=["microsoft", "google"])  # type: ignore[method-assign]
    service._delete_config_keys = AsyncMock()
    service.set_login_preference = AsyncMock()  # type: ignore[method-assign]

    assert await service.delete_provider_config("microsoft") is True
    reset = service.set_login_preference.call_args.args[0]
    assert reset.password_login_enabled is False
    assert reset.passkey_login_enabled is False
    assert reset.default_sso_provider is None
