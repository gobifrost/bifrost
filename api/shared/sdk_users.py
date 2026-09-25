"""Shared business service for the five fixed SDK user operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/users.py``) serving SDK/CLI
  callers, and
- a future engine-local dispatcher serving workflow children through
  the parent-side local transport.

Both paths share DTOs, query parameters, status/error precedence,
pagination, invite creation, audit, role transitions, self/system
protection, and transaction behavior. Each caller must enforce
platform-admin authority before invoking these operations (HTTP keeps
``CurrentSuperuser``; the future local dispatcher enforces
token-equivalent superuser authority).

Scope is the five SDK methods: ``list``, ``create``, ``get``,
``update``, ``delete``. Invite-only endpoints, bulk operations, roles,
and forms keep their router-level logic.

Parent-side only: imports SQLAlchemy models and the invite service. The
child never imports this module.

The service takes an explicit trusted session and actor arguments —
never a ``Request``, JWT, or child claims. HTTP authentication,
``HTTPException`` mapping, and the ``X-Total-Count`` header stay in the
router.

SDK/HTTP list mismatch (preserved, not fixed here): the Python SDK
``users.list`` sends ``org_id`` (see ``api/bifrost/users.py``) but the
HTTP handler accepts ``scope`` (omit / ``'global'`` / org UUID) and
ignores unknown ``org_id`` query params — so an SDK ``org_id`` filter is
currently a no-op and the call returns the unfiltered (superuser) list.
This extraction preserves current HTTP behavior exactly; a joint
HTTP/local correction stage should align the SDK ``org_id`` with the
``scope`` filter in both transports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PROVIDER_ORG_ID
from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType, resolve_org_filter
from src.core.principal import UserPrincipal

if TYPE_CHECKING:
    from src.models import UserPublic

logger = logging.getLogger(__name__)


class UserServiceError(Exception):
    """User operation failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and the future local dispatcher (``ok: false`` frames) can map the
    same failure to their own transport.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def _resolve_user(session: AsyncSession, user_id: str):
    """Load a user by UUID with email fallback. Returns None when missing."""
    from src.models import User as UserORM

    try:
        uuid_id = UUID(user_id)
        result = await session.execute(select(UserORM).where(UserORM.id == uuid_id))
    except ValueError:
        result = await session.execute(select(UserORM).where(UserORM.email == user_id))
    return result.scalar_one_or_none()


async def list_users(
    session: AsyncSession,
    principal: UserPrincipal,
    *,
    type: str | None = None,
    scope: str | None = None,
    include_inactive: bool = False,
    search: str | None = None,
    sort_by: Literal["name", "email", "status", "created", "last_login"] | None = None,
    sort_direction: Literal["asc", "desc"] = "asc",
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[UserPublic], int]:
    """List users with the historical handler behavior.

    Returns ``(items, total)`` — the caller sets the ``X-Total-Count``
    header from ``total``. System users are excluded, inactive users are
    excluded unless ``include_inactive`` is set, and each row carries its
    invite status. Defaults match the historical handler (legacy email
    order, unbounded when ``limit`` is None).

    Raises:
        UserServiceError: 422 for a malformed scope value.
    """
    from src.models import User as UserORM
    from src.models import UserPublic
    from src.models.orm import UserInvite, UserOAuthAccount
    from src.services.user_invite_service import UserInviteService

    try:
        filter_type, filter_org = resolve_org_filter(principal, scope)
    except ValueError as e:
        raise UserServiceError(422, str(e)) from None

    query = select(UserORM).where(UserORM.is_system.is_(False))

    if not include_inactive:
        query = query.where(UserORM.is_active.is_(True))

    if type:
        if type.lower() == "platform":
            query = query.where(UserORM.is_superuser.is_(True))
        elif type.lower() == "org":
            query = query.where(UserORM.is_superuser.is_(False))

    # Users don't cascade like configs: org filters pin to one org.
    if filter_type == OrgFilterType.GLOBAL_ONLY:
        query = query.where(UserORM.organization_id.is_(None))
    elif filter_type == OrgFilterType.ORG_ONLY and filter_org is not None:
        query = query.where(UserORM.organization_id == filter_org)
    elif filter_type == OrgFilterType.ORG_PLUS_GLOBAL and filter_org is not None:
        query = query.where(UserORM.organization_id == filter_org)
    # ALL: no filter applied

    if search and (term := search.strip()):
        pattern = f"%{term}%"
        query = query.where(
            or_(UserORM.name.ilike(pattern), UserORM.email.ilike(pattern))
        )

    total = await session.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())
    )

    active_account = or_(
        UserORM.is_registered.is_(True),
        exists(
            select(UserOAuthAccount.id).where(UserOAuthAccount.user_id == UserORM.id)
        ),
    )
    pending_invite = exists(
        select(UserInvite.id).where(
            UserInvite.user_id == UserORM.id,
            UserInvite.revoked_at.is_(None),
            UserInvite.expires_at >= datetime.now(timezone.utc),
        )
    )
    expired_invite = exists(
        select(UserInvite.id).where(
            UserInvite.user_id == UserORM.id,
            UserInvite.revoked_at.is_(None),
            UserInvite.expires_at < datetime.now(timezone.utc),
        )
    )
    status_order = case(
        (active_account, 0),
        (expired_invite, 1),
        (pending_invite, 3),
        else_=2,
    )
    sort_expression = {
        "name": func.coalesce(UserORM.name, UserORM.email),
        "email": UserORM.email,
        "status": status_order,
        "created": UserORM.created_at,
        "last_login": UserORM.last_login,
    }.get(sort_by or "email", UserORM.email)
    if sort_direction == "desc":
        sort_expression = sort_expression.desc()
    else:
        sort_expression = sort_expression.asc()
    query = query.order_by(sort_expression, UserORM.id.asc())
    if limit is not None:
        query = query.offset(offset).limit(limit)

    result = await session.execute(query)
    users = result.scalars().all()

    invite_svc = UserInviteService(session)
    statuses = await invite_svc.statuses_for(list(users))
    out: list[UserPublic] = []
    for u in users:
        public = UserPublic.model_validate(u)
        public.invite_status = statuses[u.id]
        out.append(public)
    return out, total or 0


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    name: str | None,
    is_active: bool = True,
    is_superuser: bool = False,
    is_external: bool = False,
    organization_id: UUID | None = None,
    actor_user_id: UUID | None,
) -> UserPublic:
    """Create a user, its invite, and the audit row.

    Mirrors the historical handler: no password is set, the user is
    trusted-verified but unregistered, and the response carries a pending
    invite status plus the one-time registration URL. No
    ``user.invited`` event is emitted here (like the handler).
    """
    from src.config import get_settings
    from src.models import User as UserORM
    from src.models import UserPublic
    from src.models.contracts.user_invites import InviteStatus
    from src.services.audit import emit_audit
    from src.services.user_invite_service import UserInviteService

    now = datetime.now(timezone.utc)

    new_user = UserORM(
        email=email,
        name=name,
        hashed_password="",
        is_active=is_active,
        is_superuser=is_superuser,
        is_external=is_external,
        is_verified=True,
        is_registered=False,
        organization_id=organization_id,
        created_at=now,
        updated_at=now,
    )

    session.add(new_user)
    await session.flush()
    await session.refresh(new_user)

    logger.info(f"Created user {new_user.email} (id: {new_user.id})")
    await emit_audit(
        session,
        "user.create",
        resource_type="user",
        resource_id=new_user.id,
        details={
            "email": new_user.email,
            "is_superuser": new_user.is_superuser,
            "organization_id": str(new_user.organization_id)
            if new_user.organization_id
            else None,
        },
    )

    svc = UserInviteService(session)
    raw_token, _invite = await svc.create_or_replace(
        user_id=new_user.id, created_by=actor_user_id
    )
    registration_url = (
        f"{get_settings().public_url.rstrip('/')}/accept-invite?token={raw_token}"
    )

    response = UserPublic.model_validate(new_user)
    response.invite_status = InviteStatus.PENDING
    response.registration_url = registration_url
    return response


async def get_user(
    session: AsyncSession,
    *,
    user_id: str,
) -> UserPublic:
    """Get a user by UUID with email fallback.

    Raises:
        UserServiceError: 404 when the user is missing.
    """
    from src.models import UserPublic

    db_user = await _resolve_user(session, user_id)
    if not db_user:
        raise UserServiceError(404, "User not found")
    return UserPublic.model_validate(db_user)


async def update_user(
    session: AsyncSession,
    *,
    user_id: str,
    email: str | None = None,
    name: str | None = None,
    password: str | None = None,
    is_active: bool | None = None,
    is_superuser: bool | None = None,
    is_verified: bool | None = None,
    is_external: bool | None = None,
    mfa_enabled: bool | None = None,
    organization_id: UUID | None = None,
) -> UserPublic:
    """Update user properties including role transitions.

    Historical quirks preserved: ``password`` is accepted for audit
    parity but never applied (the handler never set it), promoting to
    platform admin moves the user to the provider org, and
    ``organization_id=None`` means "no change" (the org cannot be
    cleared here).

    Raises:
        UserServiceError: 404 when missing, 403 for the system user.
    """
    from src.models import UserPublic
    from src.services.audit import emit_audit

    db_user = await _resolve_user(session, user_id)
    if not db_user:
        raise UserServiceError(404, "User not found")

    if db_user.is_system:
        raise UserServiceError(403, "System user cannot be modified")

    if email is not None:
        db_user.email = email
    if name is not None:
        db_user.name = name
    # password is intentionally not applied (historical behavior).
    if is_active is not None:
        db_user.is_active = is_active
    if is_superuser is not None:
        db_user.is_superuser = is_superuser
        if is_superuser:
            db_user.organization_id = PROVIDER_ORG_ID
    if is_verified is not None:
        db_user.is_verified = is_verified
    if is_external is not None:
        db_user.is_external = is_external
    if mfa_enabled is not None:
        db_user.mfa_enabled = mfa_enabled
    if organization_id is not None:
        db_user.organization_id = organization_id

    db_user.updated_at = datetime.now(timezone.utc)

    await session.flush()
    await session.refresh(db_user)

    logger.info(f"Updated user {log_safe(user_id)}")
    changed_fields = [
        k
        for k, v in (
            ("email", email),
            ("name", name),
            ("password", password),
            ("is_active", is_active),
            ("is_superuser", is_superuser),
            ("is_verified", is_verified),
            ("is_external", is_external),
            ("mfa_enabled", mfa_enabled),
            ("organization_id", organization_id),
        )
        if v is not None
    ]
    await emit_audit(
        session,
        "user.update",
        resource_type="user",
        resource_id=db_user.id,
        details={"email": db_user.email, "changed_fields": changed_fields},
    )
    return UserPublic.model_validate(db_user)


async def delete_user(
    session: AsyncSession,
    *,
    user_id: str,
    actor_user_id: UUID,
    actor_email: str,
) -> UUID:
    """Permanently delete a user.

    Error precedence (unchanged from the handler): self-delete is
    refused (400) before existence (404) and system protection (403)
    are checked.

    Returns the deleted user's id. Raises:
        UserServiceError: 400 for self-delete, 404 when missing,
            403 for the system user.
    """
    from src.services.audit import emit_audit

    if user_id == str(actor_user_id) or user_id == actor_email:
        raise UserServiceError(400, "Cannot delete yourself")

    db_user = await _resolve_user(session, user_id)
    if not db_user:
        raise UserServiceError(404, "User not found")

    if db_user.is_system:
        raise UserServiceError(403, "System user cannot be deleted")

    deleted_id = db_user.id
    deleted_email = db_user.email
    await session.delete(db_user)
    await session.flush()
    logger.info(f"Permanently deleted user {log_safe(user_id)}")
    await emit_audit(
        session,
        "user.delete",
        resource_type="user",
        resource_id=deleted_id,
        details={"email": deleted_email},
    )
    return deleted_id
