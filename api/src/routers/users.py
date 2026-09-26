"""
Users Router

List and manage users, view user roles and forms.
"""

import logging
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from src.config import get_settings
from src.core.auth import CurrentSuperuser
from src.core.db_deps import DbSession
from src.services.audit import emit_audit
from src.services.events import emit_event
from src.services.user_invite_service import UserInviteService
from src.models import User as UserORM, UserRole as UserRoleORM, FormRole as FormRoleORM
from src.models import (
    BulkUserFailure,
    BulkUserOperation,
    BulkUserResponse,
    UserCreate,
    UserPublic,
    UserUpdate,
    UserRolesResponse,
    UserFormsResponse,
)
from src.models.contracts.user_invites import (
    CreateInviteResponse,
    SendInviteRequest,
)
from src.core.constants import PROVIDER_ORG_ID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get(
    "",
    response_model=list[UserPublic],
    summary="List users",
    description="List all users with optional filtering by type and organization",
)
async def list_users(
    user: CurrentSuperuser,
    db: DbSession,
    response: Response,
    type: str | None = Query(None, description="Filter by user type: 'platform' or 'org'"),
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
        "or org UUID for specific org."
    ),
    include_inactive: bool = Query(False, description="Include inactive (disabled) users"),
    search: str | None = Query(None, description="Search user name or email"),
    sort_by: Literal["name", "email", "status", "created", "last_login"] | None = Query(
        None, description="Sort field; omit to preserve the legacy email order"
    ),
    sort_direction: Literal["asc", "desc"] = Query("asc"),
    limit: int | None = Query(
        None,
        ge=1,
        le=100,
        description="Maximum rows to return; omit for the legacy unbounded response",
    ),
    offset: int = Query(0, ge=0, description="Rows to skip when limit is set"),
) -> list[UserPublic]:
    """List users with optional filtering.

    Superusers can filter by scope or see all users.
    Note: Users are not org-scoped resources - they belong to one org.
    """
    from shared.sdk_users import UserServiceError, list_users as list_users_service

    try:
        items, total = await list_users_service(
            db,
            user,
            type=type,
            scope=scope,
            include_inactive=include_inactive,
            search=search,
            sort_by=sort_by,
            sort_direction=sort_direction,
            limit=limit,
            offset=offset,
        )
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    response.headers["X-Total-Count"] = str(total)
    return items


@router.post(
    "",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create user",
    description="Create a new user proactively (Platform admin only)",
)
async def create_user(
    request: UserCreate,
    user: CurrentSuperuser,
    db: DbSession,
) -> UserPublic:
    """Create a new user."""
    from shared.sdk_users import create_user as create_user_service

    return await create_user_service(
        db,
        email=request.email,
        name=request.name,
        is_active=request.is_active,
        is_superuser=request.is_superuser,
        is_external=request.is_external,
        organization_id=request.organization_id,
        actor_user_id=user.user_id,
    )


@router.patch(
    "/bulk",
    response_model=BulkUserResponse,
    summary="Bulk user operation",
    description=(
        "Apply one operation (move_org, replace_roles, set_active) to a batch of users "
        "in a single transaction. Returns per-user pass/fail."
    ),
)
async def bulk_update_users(
    request: BulkUserOperation,
    actor: CurrentSuperuser,
    db: DbSession,
) -> BulkUserResponse:
    """Apply a single bulk operation across N users in one transaction."""
    succeeded: list[UUID] = []
    failed: list[BulkUserFailure] = []

    rows = await db.execute(
        select(UserORM).where(UserORM.id.in_(request.user_ids))
    )
    users_by_id = {u.id: u for u in rows.scalars().all()}

    actor_id = (
        UUID(str(actor.user_id))
        if not isinstance(actor.user_id, UUID)
        else actor.user_id
    )

    for uid in request.user_ids:
        u = users_by_id.get(uid)
        if u is None:
            failed.append(BulkUserFailure(user_id=uid, reason="User not found"))
            continue
        if u.is_system:
            failed.append(BulkUserFailure(user_id=uid, reason="System user cannot be modified"))
            continue

        if request.operation == "move_org":
            target = request.organization_id  # may be None (= platform)
            if u.is_superuser and target is not None and target != PROVIDER_ORG_ID:
                failed.append(BulkUserFailure(
                    user_id=uid,
                    reason="Platform admin must be demoted before moving to a non-provider org",
                ))
                continue
            u.organization_id = target
            u.updated_at = datetime.now(timezone.utc)
            succeeded.append(uid)

        elif request.operation == "replace_roles":
            if uid == actor_id:
                failed.append(BulkUserFailure(user_id=uid, reason="Cannot change your own roles via bulk action"))
                continue
            await db.execute(
                UserRoleORM.__table__.delete().where(UserRoleORM.user_id == uid)
            )
            for rid in (request.role_ids or []):
                db.add(UserRoleORM(user_id=uid, role_id=rid, assigned_by=str(actor_id)))
            u.updated_at = datetime.now(timezone.utc)
            succeeded.append(uid)

        elif request.operation == "set_active":
            if uid == actor_id:
                failed.append(BulkUserFailure(user_id=uid, reason="Cannot change your own active state"))
                continue
            u.is_active = bool(request.is_active)
            u.updated_at = datetime.now(timezone.utc)
            succeeded.append(uid)

    await db.flush()
    await emit_audit(
        db,
        "user.bulk_update",
        resource_type="user",
        resource_id=None,
        details={
            "operation": request.operation,
            "requested": len(request.user_ids),
            "succeeded": len(succeeded),
            "failed": len(failed),
        },
    )
    return BulkUserResponse(succeeded=succeeded, failed=failed)


@router.post(
    "/{user_id}/invite/resend",
    response_model=CreateInviteResponse,
    summary="Resend invite",
    description="Generate a fresh invite token and email it to the user.",
)
async def resend_invite(
    user_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> CreateInviteResponse:
    return await _generate_invite(user_id=user_id, actor=user, db=db, send=True)


@router.post(
    "/{user_id}/invite/send",
    response_model=CreateInviteResponse,
    summary="Send invite",
    description="Emit invite automation for an existing registration link without rotating the token.",
)
async def send_invite(
    user_id: UUID,
    request: SendInviteRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> CreateInviteResponse:
    token = _extract_invite_token(request.registration_url)
    svc = UserInviteService(db)
    try:
        invite, target = await svc.get_valid_invite_user(token=token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invite is not valid") from exc

    if target.id != user_id:
        raise HTTPException(status_code=400, detail="Invite does not belong to user")

    event_id = await _emit_user_invited_event(
        actor=user,
        invite=invite,
        reason="sent",
        registration_url=request.registration_url,
        target=target,
    )

    return CreateInviteResponse(
        user_id=user_id,
        expires_at=invite.expires_at,
        registration_url=request.registration_url,
        event_emitted=True,
        event_id=event_id,
    )


@router.post(
    "/{user_id}/invite/regenerate",
    response_model=CreateInviteResponse,
    summary="Regenerate invite link",
    description="Generate a fresh invite token without sending an email; returns the URL.",
)
async def regenerate_invite(
    user_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> CreateInviteResponse:
    return await _generate_invite(user_id=user_id, actor=user, db=db, send=False)


@router.delete(
    "/{user_id}/invite",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke invite",
    description="Revoke any active invite for the user.",
)
async def revoke_invite(
    user_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    svc = UserInviteService(db)
    await svc.revoke(user_id=user_id)


async def _generate_invite(
    *, user_id: UUID, actor, db, send: bool
) -> CreateInviteResponse:
    target = (
        await db.execute(select(UserORM).where(UserORM.id == user_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.is_registered:
        raise HTTPException(status_code=409, detail="User is already registered")

    svc = UserInviteService(db)
    raw_token, invite = await svc.create_or_replace(
        user_id=user_id, created_by=actor.user_id
    )
    registration_url = (
        f"{get_settings().public_url.rstrip('/')}/accept-invite?token={raw_token}"
    )

    event_id = None
    if send:
        event_id = await _emit_user_invited_event(
            actor=actor,
            invite=invite,
            reason="resent",
            registration_url=registration_url,
            target=target,
        )

    return CreateInviteResponse(
        user_id=user_id,
        expires_at=invite.expires_at,
        registration_url=registration_url,
        event_emitted=send,
        event_id=event_id,
    )


def _extract_invite_token(registration_url: str) -> str:
    parsed = urlparse(registration_url)
    token = parse_qs(parsed.query).get("token", [None])[0]
    if not token:
        raise HTTPException(status_code=400, detail="registration_url must include token")
    return token


async def _emit_user_invited_event(
    *,
    actor,
    invite,
    reason: str,
    registration_url: str,
    target: UserORM,
) -> UUID:
    event_id, _ = await emit_event(
        "user.invited",
        {
            "user_id": str(target.id),
            "email": target.email,
            "name": target.name or "",
            "registration_url": registration_url,
            "expires_at": invite.expires_at.isoformat(),
            "invited_by": {
                "user_id": str(actor.user_id),
                "email": actor.email,
                "name": getattr(actor, "name", None) or "",
            },
            "reason": reason,
        },
        organization_id=target.organization_id,
        triggered_by=str(actor.user_id),
    )
    return event_id


@router.get(
    "/{user_id}",
    response_model=UserPublic,
    summary="Get user details",
    description="Get a specific user's details (Platform admin only)",
)
async def get_user(
    user_id: str,
    user: CurrentSuperuser,
    db: DbSession,
) -> UserPublic:
    """Get a specific user's details."""
    from shared.sdk_users import UserServiceError, get_user as get_user_service

    try:
        return await get_user_service(db, user_id=user_id)
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.patch(
    "/{user_id}",
    response_model=UserPublic,
    summary="Update user",
    description="Update user properties including role transitions",
)
async def update_user(
    user_id: str,
    request: UserUpdate,
    user: CurrentSuperuser,
    db: DbSession,
) -> UserPublic:
    """Update a user."""
    from shared.sdk_users import UserServiceError, update_user as update_user_service

    try:
        return await update_user_service(
            db,
            user_id=user_id,
            email=request.email,
            name=request.name,
            password=request.password,
            is_active=request.is_active,
            is_superuser=request.is_superuser,
            is_verified=request.is_verified,
            is_external=request.is_external,
            mfa_enabled=request.mfa_enabled,
            organization_id=request.organization_id,
        )
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete user",
    description="Delete a user from the system",
)
async def delete_user(
    user_id: str,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Permanently delete a user. User must be inactive first."""
    from shared.sdk_users import UserServiceError, delete_user as delete_user_service

    try:
        await delete_user_service(
            db,
            user_id=user_id,
            actor_user_id=user.user_id,
            actor_email=user.email,
        )
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.get(
    "/{user_id}/roles",
    response_model=UserRolesResponse,
    summary="Get user roles",
    description="Get all roles assigned to a user",
)
async def get_user_roles(
    user_id: str,
    user: CurrentSuperuser,
    db: DbSession,
) -> UserRolesResponse:
    """Get all roles assigned to a user."""
    # Get user UUID
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        result = await db.execute(select(UserORM.id).where(UserORM.email == user_id))
        user_uuid = result.scalar_one_or_none()
        if not user_uuid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

    result = await db.execute(
        select(UserRoleORM.role_id).where(UserRoleORM.user_id == user_uuid)
    )
    role_ids = [str(rid) for rid in result.scalars().all()]

    return UserRolesResponse(role_ids=role_ids)


@router.get(
    "/{user_id}/forms",
    response_model=UserFormsResponse,
    summary="Get user forms",
    description="Get all forms a user can access based on their roles",
)
async def get_user_forms(
    user_id: str,
    user: CurrentSuperuser,
    db: DbSession,
) -> UserFormsResponse:
    """Get all forms a user can access."""
    # Get user
    try:
        uuid_id = UUID(user_id)
        result = await db.execute(select(UserORM).where(UserORM.id == uuid_id))
    except ValueError:
        result = await db.execute(select(UserORM).where(UserORM.email == user_id))

    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Platform admins have access to all forms
    if db_user.is_superuser:
        return UserFormsResponse(
            is_superuser=True,
            has_access_to_all_forms=True,
            form_ids=[],
        )

    # Get user's roles
    role_result = await db.execute(
        select(UserRoleORM.role_id).where(UserRoleORM.user_id == db_user.id)
    )
    role_ids = list(role_result.scalars().all())

    if not role_ids:
        return UserFormsResponse(
            is_superuser=False,
            has_access_to_all_forms=False,
            form_ids=[],
        )

    # Get forms for those roles
    form_result = await db.execute(
        select(FormRoleORM.form_id).where(FormRoleORM.role_id.in_(role_ids))
    )
    form_ids = list(set(str(fid) for fid in form_result.scalars().all()))

    return UserFormsResponse(
        is_superuser=False,
        has_access_to_all_forms=False,
        form_ids=form_ids,
    )
