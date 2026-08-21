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
from sqlalchemy.orm import selectinload

from shared.authorization_scopes import PLATFORM_SUPERUSER_SCOPE
from src.config import get_settings
from src.core.constants import PLATFORM_ADMIN_ROLE_ID, PROVIDER_ORG_ID
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationBoundaryKind,
    CurrentAuthorizationContext,
    authorize_capability,
)
from src.services.audit import emit_audit
from src.services.events import emit_event
from src.services.user_invite_service import UserInviteService
from src.services.user_provisioning import (
    ensure_platform_operator_role,
    sync_organization_member_role,
    validate_platform_admin_removal,
)
from src.services.operation_catalog import operation_route
from src.services.role_assignments import (
    RoleAssignmentBoundarySpec,
    delete_role_assignment,
    infer_legacy_role_assignment_boundaries,
    replace_role_assignment,
)
from src.services.user_authorization import (
    require_exact_user_boundary,
    require_user_visible,
)
from src.models import (
    FormRole as FormRoleORM,
    RoleAssignmentPublic,
    User as UserORM,
)
from src.models.orm.role_assignments import RoleAssignment as RoleAssignmentORM
from src.models.orm.organizations import Organization as OrganizationORM
from src.models import (
    BulkUserFailure,
    BulkUserOperation,
    BulkUserResponse,
    RoleAssignmentBoundaryInput,
    UserCreate,
    UserPublic,
    RoleAssignmentSelection,
    UserUpdate,
    UserFormsResponse,
    UserRolesResponse,
)
from src.models.contracts.user_invites import (
    CreateInviteResponse,
    InviteStatus,
    SendInviteRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get(
    "",
    response_model=list[UserPublic],
    summary="List users",
    description="List all users with optional filtering by type and organization",
    **operation_route("users.list"),
)
async def list_users(
    authorization: CurrentAuthorizationContext,
    db: DbSession,
    type: str | None = Query(
        None, description="Filter by user type: 'platform' or 'org'"
    ),
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
        "or org UUID for specific org.",
    ),
    include_inactive: bool = Query(
        False, description="Include inactive (disabled) users"
    ),
) -> list[UserPublic]:
    """List users admitted by the selected organization boundary."""
    authorization.require_operation("users.list")

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

    # The immutable wildcard preserves the established unfiltered admin view
    # when no explicit scope was selected. Every other view is boundary exact.
    if not authorization.has_capability(PLATFORM_SUPERUSER_SCOPE) or scope is not None:
        boundary = authorization.selected_boundary
        if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
            query = query.where(UserORM.organization_id == boundary.organization_id)
        elif boundary.kind is AuthorizationBoundaryKind.PLATFORM:
            query = query.where(UserORM.organization_id.is_(None))
        else:
            managed_org_ids = select(OrganizationORM.id).where(
                OrganizationORM.is_provider.is_(False)
            )
            query = query.where(UserORM.organization_id.in_(managed_org_ids))

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
    description="Create a new user in an authorized organization boundary",
    **operation_route("users.create"),
)
async def create_user(
    request: UserCreate,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> UserPublic:
    """Create a new user."""
    authorization.require_operation("users.create")
    require_exact_user_boundary(
        authorization=authorization,
        organization_id=request.organization_id,
    )
    if request.is_superuser:
        authorization.require(PLATFORM_SUPERUSER_SCOPE)
    now = datetime.now(timezone.utc)

    new_user = UserORM(
        email=request.email,
        name=request.name,
        hashed_password="",  # No password for admin-created users
        is_active=request.is_active,
        is_superuser=request.is_superuser,
        is_external=request.is_external,
        is_verified=True,  # Trusted since created by admin
        is_registered=False,  # User must complete registration to set password
        organization_id=request.organization_id,
        created_at=now,
        updated_at=now,
    )

    db.add(new_user)
    await db.flush()
    await db.refresh(new_user)
    if request.is_superuser:
        await replace_role_assignment(
            db,
            requester=authorization.effective_actor,
            user_id=new_user.id,
            role_id=PLATFORM_ADMIN_ROLE_ID,
            boundaries=[RoleAssignmentBoundarySpec(kind="platform")],
        )
    else:
        await sync_organization_member_role(
            db,
            user_id=new_user.id,
            organization_id=new_user.organization_id,
            is_system=new_user.is_system,
        )
        await ensure_platform_operator_role(
            db,
            user_id=new_user.id,
            organization_id=new_user.organization_id,
            is_superuser=new_user.is_superuser,
            is_system=new_user.is_system,
        )

    logger.info(f"Created user {new_user.email} (id: {new_user.id})")
    await emit_audit(
        db,
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

    svc = UserInviteService(db)
    raw_token, invite = await svc.create_or_replace(
        user_id=new_user.id, created_by=authorization.effective_actor.user_id
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
    **operation_route("users.bulk_update"),
)
async def bulk_update_users(
    request: BulkUserOperation,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> BulkUserResponse:
    """Apply a single bulk operation across N users in one transaction."""
    authorization.require_operation("users.bulk_update")
    succeeded: list[UUID] = []
    failed: list[BulkUserFailure] = []

    rows = await db.execute(select(UserORM).where(UserORM.id.in_(request.user_ids)))
    users_by_id = {u.id: u for u in rows.scalars().all()}

    actor = authorization.effective_actor
    actor_id = actor.user_id

    for uid in request.user_ids:
        u = users_by_id.get(uid)
        if u is None:
            failed.append(BulkUserFailure(user_id=uid, reason="User not found"))
            continue
        if u.is_system:
            failed.append(
                BulkUserFailure(user_id=uid, reason="System user cannot be modified")
            )
            continue

        try:
            source_boundary = (
                AuthorizationBoundary.organization(u.organization_id)
                if u.organization_id is not None
                else AuthorizationBoundary.platform()
            )
            await authorize_capability(
                db,
                requester=actor,
                selected_boundary=source_boundary,
                capability="organizations.readwrite",
            )
        except HTTPException as exc:
            failed.append(BulkUserFailure(user_id=uid, reason=str(exc.detail)))
            continue

        if request.operation == "move_org":
            target = request.organization_id  # may be None (= platform)
            if u.is_superuser and target is not None and target != PROVIDER_ORG_ID:
                failed.append(
                    BulkUserFailure(
                        user_id=uid,
                        reason="Platform admin must be demoted before moving to a non-provider org",
                    )
                )
                continue
            destination_boundary = (
                AuthorizationBoundary.organization(target)
                if target is not None
                else AuthorizationBoundary.platform()
            )
            try:
                await authorize_capability(
                    db,
                    requester=actor,
                    selected_boundary=destination_boundary,
                    capability="organizations.readwrite",
                )
            except HTTPException as exc:
                failed.append(BulkUserFailure(user_id=uid, reason=str(exc.detail)))
                continue
            u.organization_id = target
            u.updated_at = datetime.now(timezone.utc)
            await sync_organization_member_role(
                db,
                user_id=u.id,
                organization_id=target,
                is_system=u.is_system,
            )
            await ensure_platform_operator_role(
                db,
                user_id=u.id,
                organization_id=u.organization_id,
                is_superuser=u.is_superuser,
                is_system=u.is_system,
            )
            succeeded.append(uid)

        elif request.operation == "replace_roles":
            if uid == actor_id:
                failed.append(
                    BulkUserFailure(
                        user_id=uid,
                        reason="Cannot change your own roles via bulk action",
                    )
                )
                continue
            selections = request.role_assignments or []
            if request.role_assignments is None and request.role_ids is not None:
                inferred_boundaries = infer_legacy_role_assignment_boundaries(
                    authorization
                )
                selections = [
                    RoleAssignmentSelection(
                        role_id=role_id,
                        boundaries=[
                            RoleAssignmentBoundaryInput(
                                boundary_kind=boundary.kind,
                                organization_id=boundary.organization_id,
                                organization_group_id=boundary.organization_group_id,
                            )
                            for boundary in inferred_boundaries
                        ],
                    )
                    for role_id in request.role_ids
                ]
            selected_role_ids = {selection.role_id for selection in selections}
            existing_role_ids = set(
                (
                    await db.execute(
                        select(RoleAssignmentORM.role_id).where(
                            RoleAssignmentORM.user_id == uid
                        )
                    )
                )
                .scalars()
                .all()
            )
            if (
                PLATFORM_ADMIN_ROLE_ID in existing_role_ids
                and PLATFORM_ADMIN_ROLE_ID not in selected_role_ids
            ):
                try:
                    await validate_platform_admin_removal(
                        db,
                        user_ids=[uid],
                        actor_user_id=actor_id,
                    )
                except ValueError as exc:
                    failed.append(BulkUserFailure(user_id=uid, reason=str(exc)))
                    continue
            try:
                for role_id in existing_role_ids - selected_role_ids:
                    await delete_role_assignment(
                        db,
                        requester=actor,
                        user_id=uid,
                        role_id=role_id,
                    )
                for selection in selections:
                    await replace_role_assignment(
                        db,
                        requester=actor,
                        user_id=uid,
                        role_id=selection.role_id,
                        boundaries=[
                            RoleAssignmentBoundarySpec(
                                kind=boundary.boundary_kind,
                                organization_id=boundary.organization_id,
                                organization_group_id=boundary.organization_group_id,
                            )
                            for boundary in selection.boundaries
                        ],
                    )
            except HTTPException as exc:
                failed.append(BulkUserFailure(user_id=uid, reason=str(exc.detail)))
                continue
            u.is_superuser = PLATFORM_ADMIN_ROLE_ID in selected_role_ids
            u.updated_at = datetime.now(timezone.utc)
            succeeded.append(uid)

        elif request.operation == "set_active":
            if uid == actor_id:
                failed.append(
                    BulkUserFailure(
                        user_id=uid, reason="Cannot change your own active state"
                    )
                )
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
    **operation_route("users.invites.resend"),
)
async def resend_invite(
    user_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> CreateInviteResponse:
    authorization.require_operation("users.invites.resend")
    return await _generate_invite(
        user_id=user_id,
        authorization=authorization,
        db=db,
        send=True,
    )


@router.post(
    "/{user_id}/invite/send",
    response_model=CreateInviteResponse,
    summary="Send invite",
    description="Emit invite automation for an existing registration link without rotating the token.",
    **operation_route("users.invites.send"),
)
async def send_invite(
    user_id: UUID,
    request: SendInviteRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> CreateInviteResponse:
    authorization.require_operation("users.invites.send")
    token = _extract_invite_token(request.registration_url)
    svc = UserInviteService(db)
    try:
        invite, target = await svc.get_valid_invite_user(token=token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invite is not valid") from exc

    if target.id != user_id:
        raise HTTPException(status_code=400, detail="Invite does not belong to user")
    require_exact_user_boundary(
        authorization=authorization,
        organization_id=target.organization_id,
    )

    event_id = await _emit_user_invited_event(
        actor=authorization.effective_actor,
        invite=invite,
        reason="sent",
        registration_url=request.registration_url,
        target=target,
    )
    await emit_audit(
        db,
        "user.invite_send",
        resource_type="user",
        resource_id=user_id,
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
    **operation_route("users.invites.regenerate"),
)
async def regenerate_invite(
    user_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> CreateInviteResponse:
    authorization.require_operation("users.invites.regenerate")
    return await _generate_invite(
        user_id=user_id,
        authorization=authorization,
        db=db,
        send=False,
    )


@router.delete(
    "/{user_id}/invite",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke invite",
    description="Revoke any active invite for the user.",
    **operation_route("users.invites.revoke"),
)
async def revoke_invite(
    user_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    authorization.require_operation("users.invites.revoke")
    target = await db.get(UserORM, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    require_exact_user_boundary(
        authorization=authorization,
        organization_id=target.organization_id,
    )
    svc = UserInviteService(db)
    await svc.revoke(user_id=user_id)
    await emit_audit(
        db,
        "user.invite_revoke",
        resource_type="user",
        resource_id=user_id,
    )


async def _generate_invite(
    *,
    user_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
    send: bool,
) -> CreateInviteResponse:
    target = (
        await db.execute(select(UserORM).where(UserORM.id == user_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    require_exact_user_boundary(
        authorization=authorization,
        organization_id=target.organization_id,
    )
    if target.is_registered:
        raise HTTPException(status_code=409, detail="User is already registered")

    svc = UserInviteService(db)
    raw_token, invite = await svc.create_or_replace(
        user_id=user_id,
        created_by=authorization.effective_actor.user_id,
    )
    registration_url = (
        f"{get_settings().public_url.rstrip('/')}/accept-invite?token={raw_token}"
    )

    event_id = None
    if send:
        event_id = await _emit_user_invited_event(
            actor=authorization.effective_actor,
            invite=invite,
            reason="resent",
            registration_url=registration_url,
            target=target,
        )
    await emit_audit(
        db,
        "user.invite_resend" if send else "user.invite_regenerate",
        resource_type="user",
        resource_id=user_id,
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
        raise HTTPException(
            status_code=400, detail="registration_url must include token"
        )
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
    description="Get a user admitted by the active organization boundary",
    **operation_route("users.get"),
)
async def get_user(
    user_id: str,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> UserPublic:
    """Get a specific user's details."""
    authorization.require_operation("users.get")
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

    await require_user_visible(
        db,
        authorization=authorization,
        user=db_user,
    )

    return UserPublic.model_validate(db_user)


@router.patch(
    "/{user_id}",
    response_model=UserPublic,
    summary="Update user",
    description="Update user properties including role transitions",
    **operation_route("users.update"),
)
async def update_user(
    user_id: str,
    request: UserUpdate,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> UserPublic:
    """Update a user."""
    authorization.require_operation("users.update")
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

    require_exact_user_boundary(
        authorization=authorization,
        organization_id=db_user.organization_id,
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
        if request.is_superuser:
            await authorize_capability(
                db,
                requester=authorization.effective_actor,
                selected_boundary=AuthorizationBoundary.platform(),
                capability="roles.readwrite",
            )
            await replace_role_assignment(
                db,
                requester=authorization.effective_actor,
                user_id=db_user.id,
                role_id=PLATFORM_ADMIN_ROLE_ID,
                boundaries=[RoleAssignmentBoundarySpec(kind="platform")],
            )
            # The legacy column remains a materialized compatibility field
            # until non-human principals and JWT claims complete their cutover.
            db_user.is_superuser = True
            db_user.organization_id = PROVIDER_ORG_ID
        elif db_user.is_superuser:
            await validate_platform_admin_removal(
                db,
                user_ids=[db_user.id],
                actor_user_id=authorization.effective_actor.user_id,
            )
            await delete_role_assignment(
                db,
                requester=authorization.effective_actor,
                user_id=db_user.id,
                role_id=PLATFORM_ADMIN_ROLE_ID,
            )
            db_user.is_superuser = False
    if request.is_verified is not None:
        db_user.is_verified = request.is_verified
    if request.is_external is not None:
        db_user.is_external = request.is_external
    if request.mfa_enabled is not None:
        db_user.mfa_enabled = request.mfa_enabled
    if (
        "organization_id" in request.model_fields_set
        and request.is_superuser is not True
        and request.organization_id != db_user.organization_id
    ):
        destination = (
            AuthorizationBoundary.organization(request.organization_id)
            if request.organization_id is not None
            else AuthorizationBoundary.platform()
        )
        await authorize_capability(
            db,
            requester=authorization.effective_actor,
            selected_boundary=destination,
            capability="organizations.readwrite",
        )
        db_user.organization_id = request.organization_id

    if not db_user.is_superuser:
        await sync_organization_member_role(
            db,
            user_id=db_user.id,
            organization_id=db_user.organization_id,
            is_system=db_user.is_system,
        )
        await ensure_platform_operator_role(
            db,
            user_id=db_user.id,
            organization_id=db_user.organization_id,
            is_superuser=db_user.is_superuser,
            is_system=db_user.is_system,
        )
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
    **operation_route("users.delete"),
)
async def delete_user(
    user_id: str,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Permanently delete a user. User must be inactive first."""
    authorization.require_operation("users.delete")
    # Users cannot delete themselves
    if (
        user_id == str(authorization.effective_actor.user_id)
        or user_id == authorization.effective_actor.email
    ):
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

    require_exact_user_boundary(
        authorization=authorization,
        organization_id=db_user.organization_id,
    )

    if db_user.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System user cannot be deleted",
        )

    if db_user.is_superuser:
        await validate_platform_admin_removal(
            db,
            user_ids=[db_user.id],
            actor_user_id=authorization.effective_actor.user_id,
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


async def _visible_user_role_assignments(
    user_id: str,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> list[RoleAssignmentPublic]:
    selected_boundary = authorization.selected_boundary
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        result = await db.execute(
            select(UserORM.id, UserORM.organization_id).where(UserORM.email == user_id)
        )
        user_row = result.one_or_none()
        if user_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        user_uuid, user_org_id = user_row
    else:
        result = await db.execute(
            select(UserORM.id, UserORM.organization_id).where(UserORM.id == user_uuid)
        )
        user_row = result.one_or_none()
        if user_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        user_uuid, user_org_id = user_row

    if not authorization.has_capability(PLATFORM_SUPERUSER_SCOPE):
        if selected_boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
            if user_org_id != selected_boundary.organization_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found",
                )
        elif selected_boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
            if user_org_id is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found",
                )
            is_provider = await db.scalar(
                select(OrganizationORM.is_provider).where(
                    OrganizationORM.id == user_org_id
                )
            )
            if is_provider is not False:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found",
                )
        elif user_org_id is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

    result = await db.execute(
        select(RoleAssignmentORM)
        .options(selectinload(RoleAssignmentORM.boundaries))
        .where(RoleAssignmentORM.user_id == user_uuid)
        .order_by(RoleAssignmentORM.assigned_at, RoleAssignmentORM.id)
    )
    return [RoleAssignmentPublic.model_validate(row) for row in result.scalars().all()]


@router.get(
    "/{user_id}/roles",
    response_model=UserRolesResponse,
    summary="Get user roles",
    description="Get all role IDs assigned to a user",
    **operation_route("users.roles.list"),
)
async def get_user_roles(
    user_id: str,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> UserRolesResponse:
    """Get assigned role IDs using the shipped public response shape."""
    authorization.require_operation("users.roles.list")
    assignments = await _visible_user_role_assignments(user_id, authorization, db)
    return UserRolesResponse(
        role_ids=[str(assignment.role_id) for assignment in assignments]
    )


@router.get(
    "/{user_id}/role-assignments",
    response_model=list[RoleAssignmentPublic],
    summary="Get user role assignments",
    description="Get all boundary-aware role assignments for a user",
)
async def get_user_role_assignments(
    user_id: str,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> list[RoleAssignmentPublic]:
    """Get boundary-aware role assignments for the Roles UI."""
    authorization.require_operation("users.roles.list")
    return await _visible_user_role_assignments(user_id, authorization, db)


@router.get(
    "/{user_id}/forms",
    response_model=UserFormsResponse,
    summary="Get user forms",
    description="Get all forms a user can access based on their roles",
    **operation_route("users.forms.list"),
)
async def get_user_forms(
    user_id: str,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> UserFormsResponse:
    """Get all forms a user can access."""
    authorization.require_operation("users.forms.list")
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

    await require_user_visible(
        db,
        authorization=authorization,
        user=db_user,
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
        select(RoleAssignmentORM.role_id).where(RoleAssignmentORM.user_id == db_user.id)
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
