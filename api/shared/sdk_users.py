"""Shared business service for the five fixed SDK user operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/users.py``) serving SDK/CLI
  callers, and
- the same handlers reached by workflow children over the worker-local
  engine socket (``api/src/services/execution/worker_sdk_http.py``).

Both paths share DTOs, query parameters, status/error precedence,
pagination, invite creation, audit, role transitions, self/system
protection, and transaction behavior.

Authorization is decided here, with the evaluator
(``src.services.authorization.enforce``), from the ``Caller`` the handler
loads: users.read / users.readwrite / users.lifecycle.readwrite at the
target user's organization, a Platform Admin for privileged targets. Over
the engine socket the caller is an execution credential, decided as the
superuser dependency decided it: ordinary workflow engine tokens pass even
when the initiating user is not a platform admin, while supervised service
tokens do not.

Scope is the five SDK methods: ``list``, ``create``, ``get``,
``update``, ``delete``, plus the bulk operation. Invite-only endpoints,
roles, and forms keep their router-level logic.

Parent-side only: imports SQLAlchemy models and the invite service. The
child never imports this module.

The service takes an explicit trusted session and actor arguments —
never a ``Request``, JWT, or child claims. HTTP authentication,
``HTTPException`` mapping, and the ``X-Total-Count`` header stay in the
router.

SDK/HTTP list scope: the Python SDK ``users.list`` sends its
``org_id`` argument as the ``scope`` query parameter (omit / org UUID)
and the HTTP handler filters on ``scope`` — an SDK ``org_id`` filter
selects exactly that organization on both the HTTP and the local
transport.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import case, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PROVIDER_ORG_ID
from src.core.log_safety import log_safe

if TYPE_CHECKING:
    from src.models import BulkUserOperation, BulkUserResponse
    from src.models import User as UserORM
    from src.models import UserPublic
    from src.services.authorization.enforce import Caller

logger = logging.getLogger(__name__)


async def set_user_base_role(session: AsyncSession, user: "UserORM", role_id: UUID) -> None:
    """The single writer of `User.base_role_id`.

    `role_id` must be User or an existing custom (non-builtin) role. Platform
    Admin, Platform Operator and Secrets Reader are builtin roles that are
    never a base role: Platform Admin is held as an additional role, written
    by `set_platform_admin`. Raises `ValueError` otherwise.
    """
    from shared.builtin_roles import BASE_ROLE_IDS, BUILTIN_ROLE_IDS
    from src.models import Role as RoleORM

    if role_id in BUILTIN_ROLE_IDS - BASE_ROLE_IDS:
        raise ValueError(f"role_id {role_id} is a builtin role that is never a base role")
    if role_id not in BASE_ROLE_IDS:
        role = await session.get(RoleORM, role_id)
        if role is None or role.is_builtin:
            raise ValueError(f"role_id {role_id} is not a base role or a custom role")

    user.base_role_id = role_id


async def set_platform_admin(
    session: AsyncSession, user: "UserORM", is_admin: bool, *, assigned_by: str
) -> None:
    """The single writer of `User.is_superuser`.

    Every path that makes someone a Platform Admin or stops them being one
    routes through here so the two never drift: `is_superuser` is always
    exactly "the user holds the Platform Admin assignment". The assignment is
    always held at the `platform` boundary. `user` must already be flushed
    (the assignment references its id). Does nothing when the user is already
    in the requested state.
    """
    from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
    from shared.role_cache import invalidate_user as invalidate_user_role_cache
    from src.models import UserRole as UserRoleORM
    from src.services.authorization.context import Boundary, BoundaryKind
    from src.services.user_role_assignments import insert_assignment

    holds = (
        await session.scalar(
            select(UserRoleORM.user_id).where(
                UserRoleORM.user_id == user.id, UserRoleORM.role_id == PLATFORM_ADMIN_ROLE_ID
            )
        )
        is not None
    )
    if holds == is_admin:
        return
    if is_admin:
        await insert_assignment(
            session,
            user_id=user.id,
            role_id=PLATFORM_ADMIN_ROLE_ID,
            boundaries=[Boundary(BoundaryKind.PLATFORM)],
            assigned_by=assigned_by,
        )
    else:
        await session.execute(
            delete(UserRoleORM).where(
                UserRoleORM.user_id == user.id, UserRoleORM.role_id == PLATFORM_ADMIN_ROLE_ID
            )
        )
    user.is_superuser = is_admin
    await invalidate_user_role_cache(user.id)


class UserServiceError(Exception):
    """User operation failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


IDENTITY_MANAGEMENT_MESSAGE = "Identities are managed from Identities"
RESERVED_EMAIL_MESSAGE = "This email domain is reserved"


def refuse_identity_management(user: "UserORM") -> None:
    """Identities aren't edited, deleted or bulk-changed as people."""
    from shared.identities import is_identity

    if is_identity(user):
        raise UserServiceError(409, IDENTITY_MANAGEMENT_MESSAGE)


def refuse_reserved_email(email: str) -> None:
    """No person gets an email address in the identities' domain."""
    from shared.identities import is_identity_email

    if is_identity_email(email):
        raise UserServiceError(422, RESERVED_EMAIL_MESSAGE)


OPERATOR_MOVE_MESSAGE = (
    "Remove Platform Operator before moving this user out of the provider organization"
)


async def _platform_operator_holders(session: AsyncSession, user_ids: list[UUID]) -> set[UUID]:
    """The users among ``user_ids`` who hold Platform Operator at any boundary."""
    from shared.builtin_roles import PLATFORM_OPERATOR_ROLE_ID
    from src.models import UserRole as UserRoleORM

    rows = await session.execute(
        select(UserRoleORM.user_id).where(
            UserRoleORM.user_id.in_(user_ids),
            UserRoleORM.role_id == PLATFORM_OPERATOR_ROLE_ID,
        )
    )
    return set(rows.scalars())


async def _resolve_user(session: AsyncSession, user_id: str):
    """Load a user by UUID with email fallback. Returns None when missing."""
    from src.models import User as UserORM

    try:
        uuid_id = UUID(user_id)
        result = await session.execute(select(UserORM).where(UserORM.id == uuid_id))
    except ValueError:
        result = await session.execute(select(UserORM).where(UserORM.email == user_id))
    return result.scalar_one_or_none()


async def _with_protection(session: AsyncSession, users: list) -> list["UserPublic"]:
    """``UserPublic`` rows with ``is_protected`` set, in a fixed number of
    queries."""
    from src.models import UserPublic
    from src.services.authorization.enforce import privileged_user_ids

    privileged = await privileged_user_ids(session, [u.id for u in users])
    out: list[UserPublic] = []
    for u in users:
        public = UserPublic.model_validate(u)
        public.is_protected = u.id in privileged
        out.append(public)
    return out


async def list_users(
    session: AsyncSession,
    caller: "Caller",
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
    """List the users ``caller`` may read (users.read), filtered before
    counting and paging.

    Returns ``(items, total)`` — the caller sets the ``X-Total-Count``
    header from ``total``. System users are excluded, inactive users are
    excluded unless ``include_inactive`` is set, and each row carries its
    invite status. Defaults match the historical handler (legacy email
    order, unbounded when ``limit`` is None).

    ``scope``: omitted for every user the caller reaches, ``"global"`` for
    Global users only, or an organization id for exactly that organization.
    An explicit scope is decided at that target first (403 when denied).

    Raises:
        UserServiceError: 422 for a malformed scope value.
    """
    from src.models import User as UserORM
    from src.models.orm import UserInvite, UserOAuthAccount
    from src.services.authorization.enforce import (
        GLOBAL,
        cross_org,
        operation_reach,
        require_operation,
    )
    from src.services.user_invite_service import UserInviteService

    reach = operation_reach(caller, "users.list")
    query = select(UserORM).where(UserORM.is_system.is_(False), UserORM.identity_kind.is_(None))
    if scope == "global":
        require_operation(caller, "users.list", GLOBAL)
        query = query.where(UserORM.organization_id.is_(None))
    elif scope:
        try:
            scope_org = UUID(scope)
        except ValueError:
            raise UserServiceError(422, f"Invalid scope value: {scope}") from None
        require_operation(caller, "users.list", cross_org(scope_org))
        query = query.where(UserORM.organization_id == scope_org)
    reach_filter = reach.where(UserORM.organization_id)
    if reach_filter is not None:
        query = query.where(reach_filter)

    if not include_inactive:
        query = query.where(UserORM.is_active.is_(True))

    if type:
        if type.lower() == "platform":
            query = query.where(UserORM.is_superuser.is_(True))
        elif type.lower() == "org":
            query = query.where(UserORM.is_superuser.is_(False))

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
    users = list(result.scalars().all())

    invite_svc = UserInviteService(session)
    statuses = await invite_svc.statuses_for(users)
    out = await _with_protection(session, users)
    for u, public in zip(users, out):
        public.invite_status = statuses[u.id]
    return out, total or 0


async def create_user(
    session: AsyncSession,
    caller: "Caller",
    *,
    email: str,
    name: str | None,
    is_active: bool = True,
    is_superuser: bool = False,
    is_external: bool = False,
    organization_id: UUID | None = None,
) -> UserPublic:
    """Create a user, its invite, and the audit row.

    An ordinary invite (a User in an organization) is users.readwrite at
    that organization. A Global user or a Platform Admin is
    users.lifecycle.readwrite at Global, and a Platform Admin can only be
    created by a Platform Admin. The new user gets no additional roles.

    Mirrors the historical handler: no password is set, the user is
    trusted-verified but unregistered, and the response carries a pending
    invite status plus the one-time registration URL. No
    ``user.invited`` event is emitted here (like the handler).
    """
    from src.config import get_settings
    from src.models import User as UserORM
    from src.models.contracts.user_invites import InviteStatus
    from src.services.audit import emit_audit
    from src.services.authorization.enforce import (
        GLOBAL,
        cross_org,
        require_operation,
        require_unprotected,
    )
    from src.services.user_invite_service import UserInviteService
    from shared.builtin_roles import USER_ROLE_ID

    refuse_reserved_email(email)
    if is_superuser or organization_id is None:
        require_operation(
            caller, "users.create", GLOBAL, permission="users.lifecycle.readwrite"
        )
        require_unprotected(caller, is_superuser)
    else:
        require_operation(caller, "users.create", cross_org(organization_id))

    now = datetime.now(timezone.utc)

    new_user = UserORM(
        email=email,
        name=name,
        hashed_password="",
        is_active=is_active,
        # A Global user is inserted as a superuser
        # (ck_users_org_requires_superuser); set_platform_admin then adds the
        # assignment the flag stands for.
        is_superuser=is_superuser,
        is_external=is_external,
        is_verified=True,
        is_registered=False,
        organization_id=organization_id,
        created_at=now,
        updated_at=now,
    )
    await set_user_base_role(session, new_user, USER_ROLE_ID)

    session.add(new_user)
    await session.flush()
    await set_platform_admin(
        session, new_user, is_superuser, assigned_by=caller.principal.email
    )
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
        user_id=new_user.id, created_by=caller.principal.user_id
    )
    registration_url = (
        f"{get_settings().public_url.rstrip('/')}/accept-invite?token={raw_token}"
    )

    (response,) = await _with_protection(session, [new_user])
    response.invite_status = InviteStatus.PENDING
    response.registration_url = registration_url
    return response


async def get_user(
    session: AsyncSession,
    caller: "Caller",
    *,
    user_id: str,
) -> UserPublic:
    """Get a user by UUID with email fallback (users.read at their org).

    Raises:
        UserServiceError: 404 when the user is missing (only to a caller who
            reads users somewhere; anyone else gets 403 first).
    """
    from src.services.authorization.enforce import (
        operation_reach,
        org_target,
        require_operation,
    )

    operation_reach(caller, "users.get")
    db_user = await _resolve_user(session, user_id)
    if not db_user:
        raise UserServiceError(404, "User not found")
    require_operation(caller, "users.get", org_target(db_user.organization_id))
    (public,) = await _with_protection(session, [db_user])
    return public


# How each ``UserUpdate`` field is authorized: user support (users.readwrite),
# elevated lifecycle changes (users.lifecycle.readwrite), or the legacy
# Platform Admin flag, which adds or removes the Platform Admin role and so
# takes a Platform Admin. Every supplied field is authorized,
# including explicit nulls and false.
PLATFORM_ADMIN_ONLY = "platform_admin"
UPDATE_FIELD_PERMISSIONS: dict[str, str] = {
    "name": "users.readwrite",
    "is_active": "users.readwrite",
    "mfa_enabled": "users.readwrite",
    # Accepted but never applied (historical), still a support change.
    "password": "users.readwrite",
    "email": "users.lifecycle.readwrite",
    "is_verified": "users.lifecycle.readwrite",
    "is_external": "users.lifecycle.readwrite",
    # A move: authority at the source and at the destination.
    "organization_id": "users.lifecycle.readwrite",
    "is_superuser": PLATFORM_ADMIN_ONLY,
}


def update_field_permissions(fields: set[str]) -> dict[str, str]:
    """The permission each supplied field needs. An empty update still
    needs users.readwrite (it is audited as a change)."""
    unknown = fields - set(UPDATE_FIELD_PERMISSIONS)
    if unknown:
        raise ValueError(f"unclassified user fields: {sorted(unknown)}")
    if not fields:
        return {"": "users.readwrite"}
    return {field: UPDATE_FIELD_PERMISSIONS[field] for field in fields}


def _authorize_update(
    caller: "Caller",
    db_user: "UserORM",
    fields: set[str],
    organization_id: UUID | None,
    target_is_privileged: bool,
) -> None:
    from src.services.authorization.enforce import (
        org_target,
        require_operation,
        require_unprotected,
    )

    source = org_target(db_user.organization_id)
    for field, permission in sorted(update_field_permissions(fields).items()):
        if permission == PLATFORM_ADMIN_ONLY:
            require_operation(caller, "users.update", source)
            if not caller.is_platform_admin:
                raise UserServiceError(
                    403, "Only a Platform Admin can change whether a user is a Platform Admin"
                )
            continue
        require_operation(caller, "users.update", source, permission=permission)
        if field == "organization_id" and organization_id is not None:
            require_operation(caller, "users.update", org_target(organization_id))
    require_unprotected(caller, target_is_privileged)


async def update_user(
    session: AsyncSession,
    caller: "Caller",
    *,
    user_id: str,
    fields: set[str],
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

    ``fields`` names the fields the request supplied (explicit nulls and
    false included); each is authorized by ``UPDATE_FIELD_PERMISSIONS`` at
    the user's organization, a move at the destination too, and a
    privileged user only by a Platform Admin.

    Historical quirks preserved: ``password`` is accepted for audit
    parity but never applied (the handler never set it), promoting to
    platform admin moves the user to the provider org, and
    ``organization_id=None`` means "no change" (the org cannot be
    cleared here). ``is_superuser`` adds or removes the Platform Admin
    assignment and leaves the base role as it is.

    Raises:
        UserServiceError: 404 when missing, 403 for the system user, 409
            when moving a Platform Operator out of the provider organization
            or removing Platform Admin from a Global user.
    """
    from src.services.audit import emit_audit
    from src.services.authorization.enforce import (
        operation_reach,
        privileged_user_ids,
    )

    for permission in set(update_field_permissions(fields).values()) - {PLATFORM_ADMIN_ONLY}:
        operation_reach(caller, "users.update", permission=permission)
    db_user = await _resolve_user(session, user_id)
    if not db_user:
        raise UserServiceError(404, "User not found")

    if db_user.is_system:
        raise UserServiceError(403, "System user cannot be modified")
    refuse_identity_management(db_user)
    if email is not None:
        refuse_reserved_email(email)
    _authorize_update(
        caller,
        db_user,
        fields,
        organization_id,
        db_user.id in await privileged_user_ids(session, [db_user.id]),
    )
    if (
        organization_id is not None
        and organization_id != PROVIDER_ORG_ID
        and db_user.id in await _platform_operator_holders(session, [db_user.id])
    ):
        raise UserServiceError(409, OPERATOR_MOVE_MESSAGE)
    if is_superuser is False and db_user.organization_id is None and organization_id is None:
        from src.services.user_role_assignments import ADMIN_REMOVAL_MESSAGE

        raise UserServiceError(409, ADMIN_REMOVAL_MESSAGE)

    if email is not None:
        db_user.email = email
    if name is not None:
        db_user.name = name
    # password is intentionally not applied (historical behavior).
    if is_active is not None:
        db_user.is_active = is_active
    if is_superuser is not None:
        await set_platform_admin(
            session, db_user, is_superuser, assigned_by=caller.principal.email
        )
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
    (public,) = await _with_protection(session, [db_user])
    return public


async def delete_user(
    session: AsyncSession,
    caller: "Caller",
    *,
    user_id: str,
) -> UUID:
    """Permanently delete a user (users.lifecycle.readwrite at their org; a
    privileged user only by a Platform Admin).

    Error precedence (unchanged from the handler): self-delete is
    refused (400) before existence (404) and system protection (403)
    are checked.

    Returns the deleted user's id. Raises:
        UserServiceError: 400 for self-delete, 404 when missing,
            403 for the system user.
    """
    from src.services.audit import emit_audit
    from src.services.authorization.enforce import (
        operation_reach,
        org_target,
        privileged_user_ids,
        require_operation,
        require_unprotected,
    )

    actor = caller.principal
    if user_id == str(actor.user_id) or user_id == actor.email:
        raise UserServiceError(400, "Cannot delete yourself")

    operation_reach(caller, "users.delete")
    db_user = await _resolve_user(session, user_id)
    if not db_user:
        raise UserServiceError(404, "User not found")

    if db_user.is_system:
        raise UserServiceError(403, "System user cannot be deleted")
    refuse_identity_management(db_user)
    require_operation(caller, "users.delete", org_target(db_user.organization_id))
    require_unprotected(caller, db_user.id in await privileged_user_ids(session, [db_user.id]))

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


_BULK_PERMISSIONS = {
    "move_org": "users.lifecycle.readwrite",
    "replace_roles": "roleassignments.readwrite",
    "set_active": "users.readwrite",
}
BUILTIN_ROLE_BULK_MESSAGE = "Built-in roles are assigned from the user's role assignments, not in bulk"


async def bulk_update_users(
    session: AsyncSession,
    caller: "Caller",
    request: "BulkUserOperation",
) -> "BulkUserResponse":
    """Apply one operation to many users; per-user outcomes.

    Each operation is decided per target user: set_active is
    users.readwrite and move_org users.lifecycle.readwrite (at the source
    and at the destination; Global for a move into Global) at the user's
    organization, and replace_roles is roleassignments.readwrite plus the
    grant ceiling for every role added or removed. A privileged user can
    only be changed by a Platform Admin. A refused user goes to ``failed``
    with the reason; only a caller who holds the operation's permission
    nowhere is refused outright (403).

    replace_roles keeps the assignments that stay (and their boundaries),
    removes the rest, and adds the new ones at the user's home organization
    (Platform for a Global user). Built-in roles are not added here, and the
    ones a user already holds (Platform Admin, for one) are left as they are.
    """
    from shared.role_cache import invalidate_user as invalidate_user_role_cache
    from shared.system_account_guard import SYSTEM_ACCOUNT_ROLE_MESSAGE, is_system_account
    from src.models import BulkUserFailure, BulkUserResponse
    from src.models import User as UserORM
    from src.models import UserRole as UserRoleORM
    from src.services.audit import emit_audit
    from src.services.authorization.enforce import (
        PROTECTED_TARGET_MESSAGE,
        allows_operation,
        denial_message,
        held_permissions_by_user,
        operation_reach,
        org_target,
    )
    from src.services.authorization.privilege import is_privileged_principal
    from src.services.user_role_assignments import (
        RoleAssignmentError,
        check_boundaries,
        check_role_change,
        default_boundaries,
        insert_assignment,
        load_roles,
    )

    operation = "users.bulk_update"
    permission = _BULK_PERMISSIONS[request.operation]
    reach = operation_reach(caller, operation, permission=permission)
    succeeded: list[UUID] = []
    failed: list[BulkUserFailure] = []

    rows = await session.execute(select(UserORM).where(UserORM.id.in_(request.user_ids)))
    users_by_id = {u.id: u for u in rows.scalars().all()}
    held = await held_permissions_by_user(session, list(users_by_id))
    operators = (
        await _platform_operator_holders(session, list(users_by_id))
        if request.operation == "move_org"
        else set()
    )
    actor_id = caller.principal.user_id

    current_roles: dict[UUID, set[UUID]] = {}
    roles = {}
    if request.operation == "replace_roles":
        role_rows = await session.execute(
            select(UserRoleORM.user_id, UserRoleORM.role_id).where(
                UserRoleORM.user_id.in_(list(users_by_id))
            )
        )
        for user_id, role_id in role_rows.all():
            current_roles.setdefault(user_id, set()).add(role_id)
        roles = await load_roles(
            session,
            set(request.role_ids or [])
            | {role_id for ids in current_roles.values() for role_id in ids},
        )

    def fail(uid: UUID, reason: str) -> None:
        failed.append(BulkUserFailure(user_id=uid, reason=reason))

    for uid in request.user_ids:
        u = users_by_id.get(uid)
        if u is None:
            fail(uid, "User not found")
            continue
        if u.is_system:
            fail(uid, "System user cannot be modified")
            continue
        if u.identity_kind is not None:
            fail(uid, IDENTITY_MANAGEMENT_MESSAGE)
            continue
        if request.operation in ("replace_roles", "set_active") and uid == actor_id:
            fail(
                uid,
                "Cannot change your own roles via bulk action"
                if request.operation == "replace_roles"
                else "Cannot change your own active state",
            )
            continue
        if request.operation == "replace_roles" and is_system_account(uid):
            fail(uid, SYSTEM_ACCOUNT_ROLE_MESSAGE)
            continue
        if not allows_operation(
            caller, operation, org_target(u.organization_id), permission=permission
        ):
            fail(uid, denial_message(permission))
            continue
        user_held = held.get(uid, frozenset())
        if is_privileged_principal(user_held) and not caller.is_platform_admin:
            fail(uid, PROTECTED_TARGET_MESSAGE)
            continue

        if request.operation == "move_org":
            target = request.organization_id  # may be None (= Global)
            if not allows_operation(caller, operation, org_target(target), permission=permission):
                fail(uid, denial_message(permission))
                continue
            if u.is_superuser and target is not None and target != PROVIDER_ORG_ID:
                fail(uid, "Platform admin must be demoted before moving to a non-provider org")
                continue
            if uid in operators and target != PROVIDER_ORG_ID:
                fail(uid, OPERATOR_MOVE_MESSAGE)
                continue
            u.organization_id = target
            u.updated_at = datetime.now(timezone.utc)
            succeeded.append(uid)

        elif request.operation == "replace_roles":
            wanted = set(request.role_ids or [])
            have = current_roles.get(uid, set())
            added = wanted - have
            removed = {role_id for role_id in have - wanted if not roles[role_id].is_builtin}
            unknown = added - set(roles)
            if unknown:
                fail(uid, f"Role {sorted(unknown)[0]} not found")
                continue
            if any(roles[role_id].is_builtin for role_id in added):
                fail(uid, BUILTIN_ROLE_BULK_MESSAGE)
                continue
            try:
                for role_id in added | removed:
                    check_role_change(caller, roles[role_id], user_held)
                for role_id in added:
                    check_boundaries(
                        caller, reach, roles[role_id], default_boundaries(u.organization_id)
                    )
            except RoleAssignmentError as exc:
                fail(uid, exc.detail)
                continue
            if removed:
                await session.execute(
                    UserRoleORM.__table__.delete().where(
                        UserRoleORM.user_id == uid, UserRoleORM.role_id.in_(removed)
                    )
                )
            for role_id in sorted(added):
                await insert_assignment(
                    session,
                    user_id=uid,
                    role_id=role_id,
                    boundaries=default_boundaries(u.organization_id),
                    assigned_by=str(actor_id),
                )
            u.updated_at = datetime.now(timezone.utc)
            succeeded.append(uid)

        elif request.operation == "set_active":
            u.is_active = bool(request.is_active)
            u.updated_at = datetime.now(timezone.utc)
            succeeded.append(uid)

    await session.flush()
    if request.operation == "replace_roles":
        for uid in succeeded:
            await invalidate_user_role_cache(uid)
    await emit_audit(
        session,
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
