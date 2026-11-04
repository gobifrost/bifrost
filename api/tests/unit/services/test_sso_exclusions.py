"""Shared SSO exclusions, including recovery-account invariants."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from shared.sso_policy import validate_recovery_admin
from src.models.contracts.oauth_config import OAuthLoginPreference
from src.repositories.users import UserRepository
from src.services.oauth_config_service import OAuthConfigService, OAuthLoginPreferenceError


@pytest.mark.asyncio
@pytest.mark.parametrize("admin,excluded,org_excluded,expected", [
    (True, False, False, False), (True, True, False, True),
    (False, True, False, True), (False, False, True, True),
    (False, False, False, False),
])
async def test_exclusions_are_account_and_organization_specific(admin, excluded, org_excluded, expected):
    user = SimpleNamespace(id=uuid4(), organization_id=uuid4(), is_superuser=admin)
    preference = OAuthLoginPreference(
        password_login_enabled=False, passkey_login_enabled=False,
        excluded_user_ids=[user.id] if excluded else [],
        excluded_organization_ids=[user.organization_id] if org_excluded else [],
    )
    service = OAuthConfigService(AsyncMock())
    service.get_login_preference = AsyncMock(return_value=preference)
    with patch.object(UserRepository, "has_any_users", AsyncMock(return_value=True)):
        assert await service.is_password_allowed_for(user) is expected
        assert await service.is_passkey_allowed_for(user) is expected
        other = SimpleNamespace(id=uuid4(), organization_id=uuid4(), is_superuser=False)
        assert not await service.is_password_allowed_for(other)


@pytest.mark.asyncio
async def test_cannot_remove_the_last_excluded_active_admin():
    admin, other = uuid4(), uuid4()
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value = [admin, other]
    db.execute.return_value = result
    preference = OAuthLoginPreference(excluded_user_ids=[admin])
    with patch("shared.account_guards._lock_roles", AsyncMock()) as lock:
        await validate_recovery_admin(db, preference)
        with pytest.raises(OAuthLoginPreferenceError, match="active platform admin"):
            await validate_recovery_admin(db, preference, {admin})
        lock.assert_awaited()


@pytest.mark.asyncio
async def test_policy_must_include_an_active_admin_not_just_an_unknown_id():
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value = [uuid4()]
    db.execute.return_value = result
    with patch("shared.account_guards._lock_roles", AsyncMock()):
        with pytest.raises(OAuthLoginPreferenceError, match="active platform admin"):
            await validate_recovery_admin(db, OAuthLoginPreference(excluded_user_ids=[uuid4()]))


@pytest.mark.asyncio
@pytest.mark.parametrize("known_domain,excluded,expected", [
    (True, True, True), (True, False, False), (False, False, False),
])
async def test_new_registration_uses_only_resolved_organization_exclusion(known_domain, excluded, expected):
    org = SimpleNamespace(id=uuid4()) if known_domain else None
    service = OAuthConfigService(AsyncMock())
    service.get_login_preference = AsyncMock(return_value=OAuthLoginPreference(
        password_login_enabled=False,
        excluded_organization_ids=[org.id] if excluded else [],
    ))
    with (
        patch.object(UserRepository, "get_by_email", AsyncMock(return_value=None)),
        patch("src.repositories.organizations.OrganizationRepository.get_by_domain", AsyncMock(return_value=org)),
        patch.object(UserRepository, "has_any_users", AsyncMock(return_value=True)),
    ):
        assert await service.is_password_registration_allowed("new@example.com") is expected
