"""Administrator MFA reset: what it removes, who it refuses, how the user
signs in afterwards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from shared.sdk_users import UserServiceError
from src.models import User as UserORM
from src.models.orm import MFARecoveryCode, Organization, TrustedDevice, UserMFAMethod, UserOAuthAccount
from src.models.orm.mfa import MFAMethodStatus, MFAMethodType, UserPasskey
from src.services.mfa_service import MFAService
from src.services.user_mfa_reset import PASSKEY_ONLY_MESSAGE, SELF_RESET_MESSAGE, reset_user_mfa
from tests.helpers.authorization import admin_caller, handlers_as

REVOKE = "src.routers.auth.revoke_all_user_refresh_tokens"


async def _seed_user(db, *, password: str | None = "hashed", totp: bool = True, passkeys: int = 0) -> UserORM:
    org = Organization(name=f"mfa-reset-org-{uuid4().hex[:8]}", is_active=True, created_by="test")
    db.add(org)
    await db.flush()
    user = UserORM(
        email=f"mfa-reset-{uuid4().hex[:8]}@example.com",
        hashed_password=password,
        mfa_enabled=totp,
        organization_id=org.id,
    )
    db.add(user)
    await db.flush()
    if totp:
        db.add(
            UserMFAMethod(
                user_id=user.id,
                method_type=MFAMethodType.TOTP,
                status=MFAMethodStatus.ACTIVE,
                encrypted_secret="secret",
            )
        )
        db.add_all(MFARecoveryCode(user_id=user.id, code_hash=f"hash-{i}") for i in range(3))
        db.add(
            TrustedDevice(
                user_id=user.id,
                device_fingerprint="f" * 64,
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
        )
    for i in range(passkeys):
        db.add(
            UserPasskey(
                user_id=user.id,
                credential_id=uuid4().bytes,
                public_key=b"key",
                device_type="singleDevice",
                name=f"key {i}",
            )
        )
    await db.flush()
    return user


async def _count(db, model, user_id) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))


@pytest.mark.asyncio
class TestResetUserMfa:
    async def test_removes_every_second_factor_and_sessions(self, db_session):
        user = await _seed_user(db_session, passkeys=2)

        with patch(REVOKE, new=AsyncMock(return_value=4)) as revoke:
            result = await reset_user_mfa(db_session, admin_caller(), user.id)

        revoke.assert_awaited_once_with(str(user.id))
        assert result.model_dump() == {
            "totp_removed": True,
            "recovery_codes_removed": 3,
            "passkeys_removed": 2,
            "trusted_devices_revoked": 1,
            "sessions_revoked": 4,
        }
        for model in (UserMFAMethod, MFARecoveryCode, UserPasskey, TrustedDevice):
            assert await _count(db_session, model, user.id) == 0
        await db_session.refresh(user)
        assert user.mfa_enabled is False

    async def test_next_password_sign_in_must_enroll(self, db_session):
        user = await _seed_user(db_session, passkeys=1)
        with patch(REVOKE, new=AsyncMock(return_value=0)):
            await reset_user_mfa(db_session, admin_caller(), user.id)

        status = await MFAService(db_session).get_mfa_status(user)
        # /auth/login sends the user to enrollment when either is falsy.
        assert not user.mfa_enabled
        assert status["enrolled_methods"] == []

    async def test_user_without_factors_is_a_clean_no_op(self, db_session):
        user = await _seed_user(db_session, totp=False)
        with patch(REVOKE, new=AsyncMock(return_value=0)):
            result = await reset_user_mfa(db_session, admin_caller(), user.id)
        assert result.model_dump() == {
            "totp_removed": False,
            "recovery_codes_removed": 0,
            "passkeys_removed": 0,
            "trusted_devices_revoked": 0,
            "sessions_revoked": 0,
        }

    async def test_passkey_only_user_is_refused_and_keeps_everything(self, db_session):
        user = await _seed_user(db_session, password=None, totp=False, passkeys=1)
        with patch(REVOKE, new=AsyncMock()) as revoke, pytest.raises(UserServiceError) as exc:
            await reset_user_mfa(db_session, admin_caller(), user.id)
        assert exc.value.status_code == 409
        assert exc.value.detail == PASSKEY_ONLY_MESSAGE
        revoke.assert_not_awaited()
        assert await _count(db_session, UserPasskey, user.id) == 1

    async def test_passkey_user_with_linked_sso_account_may_be_reset(self, db_session):
        user = await _seed_user(db_session, password=None, totp=False, passkeys=1)
        db_session.add(
            UserOAuthAccount(
                user_id=user.id,
                provider_id="microsoft",
                provider_user_id=uuid4().hex,
                email=user.email,
            )
        )
        await db_session.flush()
        with patch(REVOKE, new=AsyncMock(return_value=0)):
            result = await reset_user_mfa(db_session, admin_caller(), user.id)
        assert result.passkeys_removed == 1
        assert await _count(db_session, UserPasskey, user.id) == 0

    async def test_refuses_own_account_missing_and_system_users(self, db_session):
        caller = admin_caller()
        with pytest.raises(UserServiceError) as exc:
            await reset_user_mfa(db_session, caller, caller.principal.user_id)
        assert (exc.value.status_code, exc.value.detail) == (400, SELF_RESET_MESSAGE)

        with pytest.raises(UserServiceError) as exc:
            await reset_user_mfa(db_session, caller, uuid4())
        assert exc.value.status_code == 404

        system = UserORM(email=f"system-{uuid4().hex[:8]}@example.com", is_system=True, is_superuser=True)
        db_session.add(system)
        await db_session.flush()
        with pytest.raises(UserServiceError) as exc:
            await reset_user_mfa(db_session, caller, system.id)
        assert (exc.value.status_code, exc.value.detail) == (403, "System user cannot be modified")


@pytest.mark.asyncio
class TestResetUserMfaRouter:
    async def test_maps_service_errors_to_http(self):
        from src.routers.users import reset_user_mfa as handler

        with handlers_as(admin_caller(), "src.routers.users"), patch(
            "src.routers.users.reset_user_mfa_service",
            new=AsyncMock(side_effect=UserServiceError(409, PASSKEY_ONLY_MESSAGE)),
        ):
            with pytest.raises(HTTPException) as exc:
                await handler(uuid4(), object(), object())
        assert (exc.value.status_code, exc.value.detail) == (409, PASSKEY_ONLY_MESSAGE)
