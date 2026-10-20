"""Support sessions cannot create durable target authentication credentials."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal


@pytest.fixture
def support_user() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="target@example.test",
        organization_id=uuid4(),
        impersonated_by_user_id=uuid4(),
        impersonation_session_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_passkey_registration_routes_reject_a_support_session(
    support_user: UserPrincipal,
) -> None:
    from src.routers import passkeys

    with patch("src.routers.passkeys.PasskeyService") as service:
        with pytest.raises(HTTPException) as options_error:
            await passkeys.get_registration_options(None, support_user, object())
        with pytest.raises(HTTPException) as verify_error:
            await passkeys.verify_registration(None, support_user, object())

    assert (options_error.value.status_code, options_error.value.detail) == (
        403,
        "Exit impersonation before managing passkeys",
    )
    assert (verify_error.value.status_code, verify_error.value.detail) == (
        403,
        "Exit impersonation before managing passkeys",
    )
    service.assert_not_called()


@pytest.mark.asyncio
async def test_mfa_enrollment_and_recovery_routes_reject_a_support_session(
    support_user: UserPrincipal,
) -> None:
    from src.routers import mfa

    with patch("src.routers.mfa.get_current_user_from_db", new=AsyncMock()) as load_user:
        with pytest.raises(HTTPException) as setup_error:
            await mfa.setup_mfa(support_user, object())
        with pytest.raises(HTTPException) as verify_error:
            await mfa.verify_mfa(mfa.MFAVerifyRequest(code="123456"), support_user, object())
        with pytest.raises(HTTPException) as recovery_error:
            await mfa.regenerate_recovery_codes(
                mfa.RegenerateRecoveryCodesRequest(mfa_code="123456"), support_user, object()
            )

    assert (setup_error.value.status_code, setup_error.value.detail) == (
        403,
        "Exit impersonation before managing MFA credentials",
    )
    assert (verify_error.value.status_code, verify_error.value.detail) == (
        403,
        "Exit impersonation before managing MFA credentials",
    )
    assert (recovery_error.value.status_code, recovery_error.value.detail) == (
        403,
        "Exit impersonation before managing MFA credentials",
    )
    load_user.assert_not_awaited()
