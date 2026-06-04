"""
Users Router

List and manage users, view user roles and forms.
"""

import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from src.config import get_settings
from src.core.auth import CurrentSuperuser
from src.core.database import DbSession
from src.core.log_safety import log_safe
from src.core.org_filter import resolve_org_filter, OrgFilterType
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
    InviteStatus,
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
    type: str | None = Query(None, description="Filter by user type: 'platform' or 'org'"),
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
        "or org UUID for specific org."
    ),
    include_inactive: bool = Query(False, description="Include inactive (disabled) users"),
) -> list[UserPublic]:
    """List users with optional filtering.

    Superusers can filter by scope or see all users.
    Note: Users are not org-scoped resources - they belong to one org.
    """
    # Resolve organization filter based on user permissions
    try:
        filter_type, filter_org = resolve_org_filter(user, scope)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    # Filter out system users - never visible in the UI
    query = select(UserORM).where(
        UserORM.is_system.is_(False),
    )

    # By default only show active users
    if not include_inactive:
        query = query.where(UserORM.is_active.is_(True))

    if type:
        if type.lower() == "platform":
            query = query.where(UserORM.is_superuser.is_(True))
        elif type.lower() == "org":
            query = query.where(UserORM.is_superuser.is_(False))

    # Apply org filter based on scope
    # For users, GLOBAL_ONLY means users without an org (platform admins)
    # ORG_ONLY and ORG_PLUS_GLOBAL filter to specific org
    if filter_type == OrgFilterType.GLOBAL_ONLY:
        query = query.where(UserORM.organization_id.is_(None))
    elif filter_type == OrgFilterType.ORG_ONLY and filter_org is not None:
        # Platform admin filtering to specific org - just that org's users
        query = query.where(UserORM.organization_id == filter_org)
    elif filter_type == OrgFilterType.ORG_PLUS_GLOBAL and filter_org is not None:
        # Org user - their org's users only (users don't cascade like configs)
        query = query.where(UserORM.organization_id == filter_org)
    # ALL: no filter applied

    query = query.order_by(UserORM.email)

    result = await db.execute(query)
    users = result.scalars().all()

    invite_svc = UserInviteService(db)
    out: list[UserPublic] = []
    for u in users:
        public = UserPublic.model_validate(u)
        public.invite_status = await invite_svc.status_for(u)
        out.append(public)
    return out


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
    now = datetime.now(timezone.utc)

    new_user = UserORM(
        email=request.email,
        name=request.name,
        hashed_password="",  # No password for admin-created users
        is_active=request.is_active,
        is_superuser=request.is_superuser,
        is_verified=True,  # Trusted since created by admin
        is_registered=False,  # User must complete registration to set password
        organization_id=request.organization_id,
        created_at=now,
        updated_at=now,
    )

    db.add(new_user)
    await db.flush()
    await db.refresh(new_user)

    logger.info(f"Created user {new_user.email} (id: {new_user.id})")
    await emit_audit(
        db,
        "user.create",
        resource_type="user",
        resource_id=new_user.id,
        details={
            "email": new_user.email,
            "is_superuser": new_user.is_superuser,
            "organization_id": str(new_user.organization_id) if new_user.organization_id else None,
        },
    )

    svc = UserInviteService(db)
    raw_token, invite = await svc.create_or_replace(
        user_id=new_user.id, created_by=user.user_id
    )
    invite_status = InviteStatus.PENDING
    registration_url = (
        f"{get_settings().public_url.rstrip('/')}/accept-invite?token={raw_token}"
    )

    response = UserPublic.model_validate(new_user)
    response.invite_status = invite_status
    response.registration_url = registration_url
    return response


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
    # Try UUID first
    try:
        uuid_id = UUID(user_id)
        result = await db.execute(select(UserORM).where(UserORM.id == uuid_id))
    except ValueError:
        # Fall back to email lookup
        result = await db.execute(select(UserORM).where(UserORM.email == user_id))

    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserPublic.model_validate(db_user)


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
    # Try UUID first
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

    # Protect system user from modification
    if db_user.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System user cannot be modified",
        )

    if request.email is not None:
        db_user.email = request.email
    if request.name is not None:
        db_user.name = request.name
    if request.is_active is not None:
        db_user.is_active = request.is_active
    if request.is_superuser is not None:
        db_user.is_superuser = request.is_superuser
        if request.is_superuser:
            # Promoting to platform admin - move to provider org
            db_user.organization_id = PROVIDER_ORG_ID
    if request.is_verified is not None:
        db_user.is_verified = request.is_verified
    if request.mfa_enabled is not None:
        db_user.mfa_enabled = request.mfa_enabled
    if request.organization_id is not None:
        db_user.organization_id = request.organization_id

    db_user.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(db_user)

    logger.info(f"Updated user {log_safe(user_id)}")
    changed_fields = [
        k for k, v in request.model_dump(exclude_unset=True).items() if v is not None
    ]
    await emit_audit(
        db,
        "user.update",
        resource_type="user",
        resource_id=db_user.id,
        details={"email": db_user.email, "changed_fields": changed_fields},
    )
    return UserPublic.model_validate(db_user)


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
    # Users cannot delete themselves
    if user_id == str(user.user_id) or user_id == user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    # Try UUID first
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

    if db_user.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System user cannot be deleted",
        )

    deleted_id = db_user.id
    deleted_email = db_user.email
    await db.delete(db_user)
    await db.flush()
    logger.info(f"Permanently deleted user {log_safe(user_id)}")
    await emit_audit(
        db,
        "user.delete",
        resource_type="user",
        resource_id=deleted_id,
        details={"email": deleted_email},
    )


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
