"""Identities API: list, create, rename and delete.

Every organization has a default identity, there is one global default, and
admins add custom ones (see ``shared.identities``); only custom ones are
renamed or deleted. Reading is ``users.read`` at the identity's organization;
creating, renaming and deleting are ``users.lifecycle.readwrite`` there
(Global for a Global identity), decided by the evaluator. Roles are assigned
through the user role-assignment routes.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
from shared.identities import IDENTITY_EMAIL_DOMAIN, is_identity, run_identity_allowed
from shared.sdk_users import set_user_base_role
from src.models.contracts.identities import (
    IdentityBaseRole,
    IdentityCreate,
    IdentityPublic,
    IdentityRole,
    IdentityUpdate,
)
from src.models.contracts.role_assignments import RoleBoundaryPublic
from src.models.enums import IdentityKind
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole, UserRoleBoundary
from src.models.orm.workflows import Workflow
from src.services.audit import emit_audit
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    build_authorization_context,
)
from src.services.authorization.enforce import (
    Caller,
    cross_org,
    operation_reach,
    org_target,
    permitted_organizations,
    privileged_user_ids,
    require_operation,
    require_unprotected,
)
from src.services.authorization.reach import OrgReach

LIST_OPERATION = "GET /api/identities"
CREATE_OPERATION = "POST /api/identities"
UPDATE_OPERATION = "PATCH /api/identities/{identity_id}"
DELETE_OPERATION = "DELETE /api/identities/{identity_id}"

DEFAULT_DELETE_MESSAGE = "Default identities can't be deleted"
DEFAULT_RENAME_MESSAGE = "Default identities can't be renamed"
# Identity names are unique per organization (Global counts as one), ignoring case.
NAME_INDEX = "uq_users_identity_name_per_org"
_NAMED_WORKFLOWS = 10

_KIND_ORDER = case(
    (User.identity_kind == IdentityKind.GLOBAL_DEFAULT, 0),
    (User.identity_kind == IdentityKind.ORG_DEFAULT, 1),
    else_=2,
)


class IdentityError(Exception):
    """A refused identity operation, with an HTTP-style status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def _workflows_using(session: AsyncSession, identities: Sequence[User]) -> dict[UUID, int]:
    """Workflows that run unattended as each identity.

    Those that name it, plus, for a default identity, those of its
    organization (Global: of none) that name no identity; the same rule
    `shared.run_lineage.unattended_lineage` resolves a run by.
    """
    ids = [identity.id for identity in identities]
    named = dict(
        (
            await session.execute(
                select(Workflow.run_identity_id, func.count())
                .where(Workflow.run_identity_id.in_(ids))
                .group_by(Workflow.run_identity_id)
            )
        ).all()
    )
    unnamed = dict(
        (
            await session.execute(
                select(Workflow.organization_id, func.count())
                .where(Workflow.run_identity_id.is_(None))
                .group_by(Workflow.organization_id)
            )
        ).all()
    )
    return {
        identity.id: named.get(identity.id, 0)
        + (unnamed.get(identity.organization_id, 0) if identity.identity_kind != IdentityKind.CUSTOM else 0)
        for identity in identities
    }


async def _public(session: AsyncSession, identities: Sequence[User]) -> list[IdentityPublic]:
    if not identities:
        return []
    ids = [identity.id for identity in identities]
    boundary_rows = (
        await session.execute(
            select(UserRoleBoundary.user_id, UserRoleBoundary.role_id, UserRoleBoundary.kind, UserRoleBoundary.organization_id)
            .where(UserRoleBoundary.user_id.in_(ids))
        )
    ).all()
    role_rows = (
        await session.execute(
            select(UserRole.user_id, Role.id, Role.name)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(ids))
            .order_by(func.lower(Role.name), Role.id)
        )
    ).all()
    organization_ids = {i.organization_id for i in identities if i.organization_id} | {
        row.organization_id for row in boundary_rows if row.organization_id
    }
    organization_names = dict(
        (
            await session.execute(
                select(Organization.id, Organization.name).where(Organization.id.in_(organization_ids))
            )
        ).all()
    )
    base_names = dict(
        (
            await session.execute(
                select(Role.id, Role.name).where(Role.id.in_({i.base_role_id for i in identities}))
            )
        ).all()
    )
    usage = await _workflows_using(session, identities)

    boundaries: dict[tuple[UUID, UUID], list[RoleBoundaryPublic]] = {}
    for user_id, role_id, kind, organization_id in sorted(boundary_rows, key=lambda r: (r.kind, str(r.organization_id))):
        boundaries.setdefault((user_id, role_id), []).append(
            RoleBoundaryPublic(
                kind=kind,
                organization_id=organization_id,
                organization_name=organization_names.get(organization_id) if organization_id else None,
            )
        )
    roles: dict[UUID, list[IdentityRole]] = {}
    for user_id, role_id, role_name in role_rows:
        roles.setdefault(user_id, []).append(
            IdentityRole(role_id=role_id, name=role_name, boundaries=boundaries.get((user_id, role_id), []))
        )

    return [
        IdentityPublic(
            id=identity.id,
            name=identity.name or "",
            identity_kind=identity.identity_kind,
            organization_id=identity.organization_id,
            organization_name=organization_names.get(identity.organization_id) if identity.organization_id else None,
            base_role=IdentityBaseRole(id=identity.base_role_id, name=base_names[identity.base_role_id]),
            additional_roles=roles.get(identity.id, []),
            workflows_using=usage[identity.id],
        )
        for identity in identities
    ]


async def _flush_named(session: AsyncSession, identity: User, name: str) -> None:
    """Name ``identity`` and flush it; a name its organization already holds
    is a 409. The unique index decides, so two writers can't both take a
    name. The name is set inside the savepoint (opening one flushes what is
    pending), so a refusal rolls back only the savepoint."""
    organization_id = identity.organization_id
    try:
        async with session.begin_nested():
            identity.name = name
            session.add(identity)
            await session.flush()
    except IntegrityError as e:
        # SQLAlchemy chains the driver's error, which names the constraint.
        if getattr(e.orig and e.orig.__cause__, "constraint_name", None) != NAME_INDEX:
            raise
        place = (
            await session.scalar(select(Organization.name).where(Organization.id == organization_id))
            if organization_id
            else "Global"
        )
        raise IdentityError(409, f'An identity named "{name}" already exists in {place}') from None


def _identities_query():
    """Every identity: Global first, then by organization name, default before custom, then by name."""
    return (
        select(User)
        .outerjoin(Organization, Organization.id == User.organization_id)
        .where(User.identity_kind.is_not(None))
        .order_by(
            User.organization_id.is_not(None), func.lower(Organization.name), _KIND_ORDER, func.lower(User.name), User.id
        )
    )


async def list_identities(
    session: AsyncSession, caller: Caller, *, organization_id: UUID | None = None
) -> list[IdentityPublic]:
    """The identities ``caller`` may read (users.read), Global first, then by
    organization; ``organization_id`` pins one organization (decided at that
    target first)."""
    reach = operation_reach(caller, LIST_OPERATION)
    query = _identities_query()
    if organization_id is not None:
        require_operation(caller, LIST_OPERATION, cross_org(organization_id))
        query = query.where(User.organization_id == organization_id)
    reach_filter = reach.where(User.organization_id)
    if reach_filter is not None:
        query = query.where(reach_filter)
    identities = list((await session.execute(query)).scalars())
    return await _public(session, identities)


async def list_run_identities(session: AsyncSession, workflow_organization_id: UUID | None) -> list[IdentityPublic]:
    """The identities a workflow of ``workflow_organization_id`` may run as
    (``run_identity_allowed``), in the order the list returns them."""
    identities = [
        identity
        for identity in (await session.execute(_identities_query())).scalars()
        if run_identity_allowed(workflow_organization_id=workflow_organization_id, identity=identity)
    ]
    return await _public(session, identities)


async def create_identity(session: AsyncSession, caller: Caller, request: IdentityCreate) -> IdentityPublic:
    """A custom identity (base role User, no additional roles) in an
    organization, or Global when ``organization_id`` is null."""
    require_operation(caller, CREATE_OPERATION, org_target(request.organization_id))
    if request.organization_id is not None and await session.get(Organization, request.organization_id) is None:
        raise IdentityError(404, "Organization not found")

    identity_id = uuid4()
    identity = User(
        id=identity_id,
        email=f"identity-{identity_id}@{IDENTITY_EMAIL_DOMAIN}",
        is_active=True,
        is_verified=True,
        is_registered=True,
        is_system=False,
        is_external=False,
        organization_id=request.organization_id,
        identity_kind=IdentityKind.CUSTOM,
    )
    await set_user_base_role(session, identity, USER_ROLE_ID)
    await _flush_named(session, identity, request.name)
    await emit_audit(
        session,
        "identity.create",
        resource_type="user",
        resource_id=identity.id,
        details={
            "name": identity.name,
            "organization_id": str(identity.organization_id) if identity.organization_id else None,
        },
    )
    (public,) = await _public(session, [identity])
    return public


async def _managed_identity(session: AsyncSession, caller: Caller, identity_id: UUID, operation: str) -> User:
    """The identity, once ``caller`` may perform ``operation`` on it; a
    privileged identity only changes at a Platform Admin's hand."""
    operation_reach(caller, operation)
    identity = await session.get(User, identity_id)
    if identity is None or not is_identity(identity):
        raise IdentityError(404, "Identity not found")
    require_operation(caller, operation, org_target(identity.organization_id))
    require_unprotected(caller, identity.id in await privileged_user_ids(session, [identity.id]))
    return identity


async def rename_identity(
    session: AsyncSession, caller: Caller, identity_id: UUID, request: IdentityUpdate
) -> IdentityPublic:
    """Rename a custom identity."""
    identity = await _managed_identity(session, caller, identity_id, UPDATE_OPERATION)
    if identity.identity_kind != IdentityKind.CUSTOM:
        raise IdentityError(409, DEFAULT_RENAME_MESSAGE)
    before = identity.name
    await _flush_named(session, identity, request.name)
    await emit_audit(
        session,
        "identity.rename",
        resource_type="user",
        resource_id=identity.id,
        details={"before": before, "after": identity.name},
    )
    (public,) = await _public(session, [identity])
    return public


async def delete_identity(session: AsyncSession, caller: Caller, identity_id: UUID) -> None:
    """Delete a custom identity no workflow runs as."""
    identity = await _managed_identity(session, caller, identity_id, DELETE_OPERATION)
    if identity.identity_kind != IdentityKind.CUSTOM:
        raise IdentityError(409, DEFAULT_DELETE_MESSAGE)
    names = (
        await session.execute(
            select(func.coalesce(Workflow.display_name, Workflow.name))
            .where(Workflow.run_identity_id == identity.id)
            .order_by(Workflow.name, Workflow.id)
        )
    ).scalars().all()
    if names:
        listed = ", ".join(names[:_NAMED_WORKFLOWS])
        more = f" and {len(names) - _NAMED_WORKFLOWS} more" if len(names) > _NAMED_WORKFLOWS else ""
        raise IdentityError(409, f"Can't delete {identity.name}: these workflows run as it: {listed}{more}")
    details = {"name": identity.name, "organization_id": str(identity.organization_id) if identity.organization_id else None}
    await session.delete(identity)
    await session.flush()
    await emit_audit(session, "identity.delete", resource_type="user", resource_id=identity_id, details=details)


def _powers(ctx: AuthorizationContext) -> list[tuple[str, Boundary]]:
    """What an identity holds beyond the User baseline every account has at its home."""
    home = (
        Boundary(BoundaryKind.PLATFORM)
        if ctx.home_organization_id is None
        else Boundary(BoundaryKind.ORGANIZATION, ctx.home_organization_id)
    )
    baseline = {(permission, home) for permission in USER_BASE_PERMISSIONS}
    return [grant for grant in ctx.effective_grants if grant not in baseline]


def _holds(reach: OrgReach, boundary: Boundary) -> bool:
    if reach.everything:
        return True
    if boundary.kind == BoundaryKind.PLATFORM:
        return reach.include_global
    if boundary.kind == BoundaryKind.MANAGED_ORGANIZATIONS:
        return reach.managed
    return reach.covers(boundary.organization_id)


async def require_delegation(session: AsyncSession, caller: Caller, identity_id: UUID) -> None:
    """403 unless ``caller`` may make a workflow run as ``identity_id``: a
    Platform Admin, or someone who holds every permission the identity holds,
    at every place it holds it. A workflow running as an identity uses its
    powers, so only someone who has them may hand them out."""
    if caller.is_platform_admin:
        return
    identity = await session.get(User, identity_id)
    assert identity is not None
    for permission, boundary in _powers(await build_authorization_context(session, identity_id)):
        if not _holds(permitted_organizations(caller, permission), boundary):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"{identity.name} holds powers you don't, so you can't make a workflow run as it",
            )
