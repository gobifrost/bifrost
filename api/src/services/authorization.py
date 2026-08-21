"""Boundary-aware authorization for human platform operations.

Capabilities answer what a person may do, the selected boundary answers where,
and domain/resource services remain responsible for deciding which concrete
objects the person may touch.  This module deliberately does not consult the
legacy ``is_superuser`` or provider-organization shortcuts.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authorization_scopes import (
    PLATFORM_SUPERUSER_SCOPE,
    implied_scopes,
)
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.core.principal import UserPrincipal
from src.models import Organization, OrganizationGroupMembership, Role, RoleAssignment
from src.models.orm.role_assignments import RoleAssignmentBoundary


class AuthorizationBoundaryKind(StrEnum):
    """Concrete request boundaries.

    Organization groups and Managed organizations are assignment selectors,
    not identities a request can execute as. They resolve to an exact
    organization boundary before authorization.
    """

    ORGANIZATION = "organization"
    MANAGED_ORGANIZATIONS = "managed_organizations"
    PLATFORM = "platform"


@dataclass(frozen=True, slots=True)
class AuthorizationBoundary:
    kind: AuthorizationBoundaryKind
    organization_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.kind is AuthorizationBoundaryKind.ORGANIZATION:
            if self.organization_id is None:
                raise ValueError("organization boundary requires organization_id")
        elif self.organization_id is not None:
            raise ValueError(f"{self.kind.value} boundary does not take organization_id")

    @classmethod
    def organization(cls, organization_id: UUID) -> "AuthorizationBoundary":
        return cls(AuthorizationBoundaryKind.ORGANIZATION, organization_id)

    @classmethod
    def platform(cls) -> "AuthorizationBoundary":
        return cls(AuthorizationBoundaryKind.PLATFORM)

    @classmethod
    def managed_organizations(cls) -> "AuthorizationBoundary":
        return cls(AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS)


@dataclass(frozen=True, slots=True)
class AuthorizationGrantSource:
    """One assignment that contributes authority in the selected boundary."""

    assignment_id: UUID
    role_id: UUID
    role_name: str
    capabilities: frozenset[str]
    covering_boundary_kind: str
    covering_boundary_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    requester: UserPrincipal
    effective_actor: UserPrincipal
    selected_boundary: AuthorizationBoundary
    effective_capabilities: frozenset[str]
    grant_sources: tuple[AuthorizationGrantSource, ...]
    request_id: str | None = None

    @property
    def role_assignment_ids(self) -> tuple[UUID, ...]:
        return tuple(source.assignment_id for source in self.grant_sources)

    @property
    def role_ids(self) -> tuple[UUID, ...]:
        return tuple(dict.fromkeys(source.role_id for source in self.grant_sources))

    def has_capability(self, capability: str) -> bool:
        return (
            PLATFORM_SUPERUSER_SCOPE in self.effective_capabilities
            or capability in self.effective_capabilities
        )

    def require(self, capability: str) -> None:
        if not self.has_capability(capability):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required capability: {capability}",
            )

    def has_delegated_capability(self, capability: str) -> bool:
        """Whether a capability represents deliberate support authority.

        A person's home-organization assignment is ordinary access, not a
        license to inspect every private resource in that organization.
        Support authority comes from the Platform Admin wildcard, a broader
        group/Managed assignment, or an explicit assignment to a different
        organization.
        """

        if not self.has_capability(capability):
            return False
        if self.has_capability(PLATFORM_SUPERUSER_SCOPE):
            return True
        boundary = self.selected_boundary
        if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
            return True
        if boundary.kind is not AuthorizationBoundaryKind.ORGANIZATION:
            return False
        if boundary.organization_id != self.requester.organization_id:
            return True
        return any(
            source.covering_boundary_kind
            in {"organization_group", "managed_organizations"}
            and capability in implied_scopes(source.capabilities)
            for source in self.grant_sources
        )

    def require_operation(self, operation_id: str) -> None:
        """Require the canonical capabilities declared for one operation."""

        from src.services.operation_catalog import get_operation

        for capability in get_operation(operation_id).action_scopes:
            self.require(capability)

    def require_resource_boundary(self, organization_id: UUID | None) -> None:
        """Require the selected request boundary to contain one resource.

        Assignment selectors such as Managed organizations are deliberately not
        executable resource identities. A customer-resource request selects the
        exact customer organization; a Global resource selects Platform.
        Collection endpoints may use Managed organizations for an explicitly
        cross-customer view, but a concrete mutation cannot.
        """

        # Platform Admin is the one immutable wildcard. Its catalog contract
        # explicitly satisfies capability, boundary, and resource checks; it
        # must not depend on a legacy home-organization accident to administer
        # a customer or Global resource.
        if self.has_capability(PLATFORM_SUPERUSER_SCOPE):
            return

        if organization_id is None:
            matches = self.selected_boundary.kind is AuthorizationBoundaryKind.PLATFORM
        else:
            matches = (
                self.selected_boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
                and self.selected_boundary.organization_id == organization_id
            )
        if not matches:
            expected = (
                "platform"
                if organization_id is None
                else f"organization:{organization_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The selected authorization boundary does not match this "
                    f"resource; select {expected}"
                ),
            )


def policy_principal_for_authorization(
    user: UserPrincipal,
    authorization: AuthorizationContext | None,
) -> UserPrincipal:
    """Return a policy-evaluation principal scoped to one human boundary.

    File/table policies use ``has_role`` against ``UserPrincipal.role_ids`` and
    ``role_names``. Legacy execution contexts may legitimately hydrate every
    role assignment because they do not resolve a human AuthorizationContext.
    Human requests that did resolve one must instead evaluate policies with
    only the grant sources covering the selected boundary, so a Role assigned
    in Org A cannot satisfy a policy in Org B or Global.

    The input principal is never mutated; public token/SDK fields remain intact.
    """

    if authorization is None:
        return user

    return replace(
        user,
        role_ids=list(authorization.role_ids),
        role_names=[
            source.role_name
            for source in authorization.grant_sources
        ],
    )


def parse_authorization_boundary(
    value: str | None,
    *,
    home_organization_id: UUID | None,
) -> AuthorizationBoundary:
    """Parse the stable request boundary header.

    ``X-Bifrost-Boundary`` accepts ``platform``, ``managed_organizations``, or
    ``organization:<uuid>``. An omitted header uses the person's home
    organization; global and cross-customer contexts are always explicit.
    """

    if value is None or not value.strip():
        if home_organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Bifrost-Boundary is required for this account",
            )
        return AuthorizationBoundary.organization(home_organization_id)

    normalized = value.strip().lower()
    if normalized == "platform":
        return AuthorizationBoundary.platform()
    if normalized == "managed_organizations":
        return AuthorizationBoundary.managed_organizations()
    prefix = "organization:"
    if normalized.startswith(prefix):
        try:
            return AuthorizationBoundary.organization(UUID(normalized[len(prefix) :]))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Bifrost-Boundary contains an invalid organization ID",
            ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "X-Bifrost-Boundary must be platform, managed_organizations, or "
            "organization:<uuid>"
        ),
    )


def _boundary_from_legacy_scope_query(value: str | None) -> str | None:
    """Translate the established ``scope`` target selector into a boundary.

    ``scope`` remains part of existing CLI/SDK contracts. It selects the same
    target as the header rather than acting as a separate authorization path.
    Omitted scope still defaults to the person's home organization.
    """

    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized == "global":
        return "platform"
    try:
        return f"organization:{UUID(normalized)}"
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope must be global or an organization UUID",
        ) from exc


async def _is_managed_organization(
    db: AsyncSession,
    organization_id: UUID,
) -> bool:
    """Whether an exact organization is a customer managed by this hoster."""

    is_provider = await db.scalar(
        select(Organization.is_provider).where(Organization.id == organization_id)
    )
    if is_provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    return not bool(is_provider)


async def _resolve_grant_sources(
    db: AsyncSession,
    *,
    user_id: UUID,
    selected_boundary: AuthorizationBoundary,
) -> tuple[AuthorizationGrantSource, ...]:
    """Load Role assignments covering exactly one active boundary."""

    rows = (
        await db.execute(
            select(RoleAssignment, Role, RoleAssignmentBoundary)
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(
                RoleAssignmentBoundary,
                RoleAssignmentBoundary.role_assignment_id == RoleAssignment.id,
            )
            .where(RoleAssignment.user_id == user_id)
        )
    ).all()

    group_ids: set[UUID] = set()
    managed = False
    if selected_boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
        assert selected_boundary.organization_id is not None
        group_ids = set(
            (
                await db.execute(
                    select(OrganizationGroupMembership.organization_group_id).where(
                        OrganizationGroupMembership.organization_id
                        == selected_boundary.organization_id
                    )
                )
            )
            .scalars()
            .all()
        )
        managed = await _is_managed_organization(
            db, selected_boundary.organization_id
        )

    sources: list[AuthorizationGrantSource] = []
    seen_assignments: set[UUID] = set()
    wildcard_assignment_ids = {
        assignment.id
        for assignment, role, boundary in rows
        if PLATFORM_SUPERUSER_SCOPE in (role.capabilities or [])
        and boundary.boundary_kind == "platform"
    }

    for assignment, role, boundary in rows:
        covers = False
        boundary_id: UUID | None = None
        if assignment.id in wildcard_assignment_ids:
            covers = True
        elif selected_boundary.kind is AuthorizationBoundaryKind.PLATFORM:
            covers = boundary.boundary_kind == "platform"
        elif selected_boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
            covers = boundary.boundary_kind == "managed_organizations"
        else:
            target_org_id = selected_boundary.organization_id
            if boundary.boundary_kind == "organization":
                covers = boundary.organization_id == target_org_id
                boundary_id = boundary.organization_id
            elif boundary.boundary_kind == "organization_group":
                covers = boundary.organization_group_id in group_ids
                boundary_id = boundary.organization_group_id
            elif boundary.boundary_kind == "managed_organizations":
                covers = managed

        if not covers or assignment.id in seen_assignments:
            continue
        seen_assignments.add(assignment.id)
        sources.append(
            AuthorizationGrantSource(
                assignment_id=assignment.id,
                role_id=role.id,
                role_name=role.name,
                capabilities=frozenset(role.capabilities or []),
                covering_boundary_kind=boundary.boundary_kind,
                covering_boundary_id=boundary_id,
            )
        )
    return tuple(sources)


async def resolve_effective_role_ids(
    db: AsyncSession,
    *,
    user_id: UUID,
    selected_boundary: AuthorizationBoundary,
) -> frozenset[UUID]:
    """Return only Roles whose assignments cover the active boundary."""

    sources = await _resolve_grant_sources(
        db,
        user_id=user_id,
        selected_boundary=selected_boundary,
    )
    return frozenset(source.role_id for source in sources)


async def resolve_authorization_context(
    db: AsyncSession,
    *,
    requester: UserPrincipal,
    selected_boundary: AuthorizationBoundary,
    request_id: str | None = None,
) -> AuthorizationContext:
    """Resolve only assignments covering ``selected_boundary``.

    The query intentionally avoids unioning all of a user's Role capabilities.
    An Organization-group or Managed-organizations selection contributes only
    while resolving one exact organization. Platform assignments contribute
    only in Platform context, except for the Platform Admin wildcard, whose
    assignment is still recorded as the source of a cross-boundary decision.
    """

    sources = await _resolve_grant_sources(
        db,
        user_id=requester.user_id,
        selected_boundary=selected_boundary,
    )

    effective = implied_scopes(
        capability
        for source in sources
        for capability in source.capabilities
    )
    return AuthorizationContext(
        requester=requester,
        effective_actor=requester,
        selected_boundary=selected_boundary,
        effective_capabilities=effective,
        grant_sources=sources,
        request_id=request_id,
    )


async def authorize_capability(
    db: AsyncSession,
    *,
    requester: UserPrincipal,
    selected_boundary: AuthorizationBoundary,
    capability: str,
    request_id: str | None = None,
) -> AuthorizationContext:
    context = await resolve_authorization_context(
        db,
        requester=requester,
        selected_boundary=selected_boundary,
        request_id=request_id,
    )
    context.require(capability)
    return context


async def authorize_operation(
    db: AsyncSession,
    *,
    requester: UserPrincipal,
    selected_boundary: AuthorizationBoundary,
    operation_id: str,
    request_id: str | None = None,
) -> AuthorizationContext:
    """Authorize every capability declared for a catalogued operation."""

    # Local import avoids making the transport-neutral operation catalog part
    # of this module's import graph during ORM/model initialization.
    from src.services.operation_catalog import get_operation

    operation = get_operation(operation_id)
    context = await resolve_authorization_context(
        db,
        requester=requester,
        selected_boundary=selected_boundary,
        request_id=request_id,
    )
    for capability in operation.action_scopes:
        context.require(capability)
    return context


async def get_authorization_context(
    request: Request,
    requester: CurrentActiveUser,
    db: DbSession,
) -> AuthorizationContext:
    """FastAPI dependency resolving a boundary-aware human context.

    Kept separate from operation authorization so handlers can resolve their
    target first and apply both the catalog capability and resource gate.
    """

    header_boundary = request.headers.get("X-Bifrost-Boundary")
    query_boundary = _boundary_from_legacy_scope_query(
        request.query_params.get("scope")
    )
    if header_boundary and query_boundary:
        parsed_header = parse_authorization_boundary(
            header_boundary,
            home_organization_id=requester.organization_id,
        )
        parsed_query = parse_authorization_boundary(
            query_boundary,
            home_organization_id=requester.organization_id,
        )
        if parsed_header != parsed_query:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="scope and X-Bifrost-Boundary select different targets",
            )
    selected_value = header_boundary or query_boundary
    return await resolve_authorization_context(
        db,
        requester=requester,
        selected_boundary=parse_authorization_boundary(
            selected_value,
            home_organization_id=requester.organization_id,
        ),
        request_id=getattr(request.state, "request_id", None),
    )


CurrentAuthorizationContext = Annotated[
    AuthorizationContext,
    Depends(get_authorization_context),
]
