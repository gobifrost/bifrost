"""Administrator MFA reset: remove every second factor a user has.

Removes the authenticator app (and its recovery codes), every passkey and
every remembered device, turns ``mfa_enabled`` off, and signs the user out
everywhere. At their next password sign-in the user is asked to enroll MFA
again (``/auth/login`` returns ``mfa_setup_required``); a reset never lets
anyone past MFA.

Decided like ``POST /auth/admin/revoke-user``: users.readwrite at the user's
organization, and a privileged user only by a Platform Admin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.sdk_users import UserServiceError
from src.models.contracts.users import UserMfaResetResponse
from src.models.orm import MFARecoveryCode, User, UserMFAMethod, UserOAuthAccount
from src.models.orm.mfa import MFAMethodType, UserPasskey
from src.services.audit import emit_audit
from src.services.authorization.enforce import (
    Caller,
    operation_reach,
    org_target,
    privileged_user_ids,
    require_operation,
    require_unprotected,
)
from src.services.mfa_service import MFAService

RESET_OPERATION = "users.mfa.reset"
SELF_RESET_MESSAGE = "Reset your own MFA from Security settings"
PASSKEY_ONLY_MESSAGE = (
    "This user signs in only with a passkey. Resetting would leave them no way to sign in."
)


async def _count(session: AsyncSession, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where)) or 0


async def reset_user_mfa(session: AsyncSession, caller: Caller, user_id: UUID) -> UserMfaResetResponse:
    """Reset ``user_id``'s MFA and sign them out of every device.

    Raises:
        HTTPException: 403 when ``caller`` may not manage the user (or the
            user is privileged and ``caller`` is not a Platform Admin).
        UserServiceError: 404 when missing, 403 for a system user, 400 for
            the caller's own account, 409 when the user has no password and
            no linked SSO account but has passkeys (they would be locked out).
    """
    from src.routers.auth import revoke_all_user_refresh_tokens

    operation_reach(caller, RESET_OPERATION)
    if user_id == caller.principal.user_id:
        raise UserServiceError(400, SELF_RESET_MESSAGE)
    user = await session.get(User, user_id)
    if user is None:
        raise UserServiceError(404, "User not found")
    if user.is_system:
        raise UserServiceError(403, "System user cannot be modified")
    require_operation(caller, RESET_OPERATION, org_target(user.organization_id))
    require_unprotected(caller, user.id in await privileged_user_ids(session, [user.id]))

    passkeys_removed = await _count(session, UserPasskey, UserPasskey.user_id == user.id)
    if (
        passkeys_removed
        and user.hashed_password is None
        and not await _count(session, UserOAuthAccount, UserOAuthAccount.user_id == user.id)
    ):
        raise UserServiceError(409, PASSKEY_ONLY_MESSAGE)

    totp_removed = bool(
        await _count(
            session,
            UserMFAMethod,
            UserMFAMethod.user_id == user.id,
            UserMFAMethod.method_type == MFAMethodType.TOTP,
        )
    )
    recovery_codes_removed = await _count(
        session, MFARecoveryCode, MFARecoveryCode.user_id == user.id
    )

    mfa = MFAService(session)
    await mfa.remove_totp(user)
    await session.execute(delete(UserPasskey).where(UserPasskey.user_id == user.id))
    trusted_devices_revoked = await mfa.revoke_all_trusted_devices(user.id)
    user.mfa_enabled = False
    user.updated_at = datetime.now(timezone.utc)
    await session.flush()

    sessions_revoked = await revoke_all_user_refresh_tokens(str(user.id))
    result = UserMfaResetResponse(
        totp_removed=totp_removed,
        recovery_codes_removed=recovery_codes_removed,
        passkeys_removed=passkeys_removed,
        trusted_devices_revoked=trusted_devices_revoked,
        sessions_revoked=sessions_revoked,
    )
    await emit_audit(
        session,
        "user.mfa_reset",
        resource_type="user",
        resource_id=user.id,
        details={"email": user.email, **result.model_dump()},
    )
    return result
