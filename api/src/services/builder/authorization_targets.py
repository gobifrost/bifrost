"""Discover the exact authorization boundaries available to Builder."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from shared.authorization_scopes import PLATFORM_SUPERUSER_SCOPE, implied_scopes
from src.core.principal import UserPrincipal
from src.models.contracts.operation_catalog import OperationTargetKind
from src.services.authorization_targets import discover_authorization_targets
from src.services.operation_catalog import OPERATION_CATALOG


@dataclass(frozen=True, slots=True)
class BuilderOrganizationAuthorization:
    id: UUID
    name: str
    is_provider: bool
    capabilities: frozenset[str]
    role_ids: frozenset[UUID]

    @property
    def can_read(self) -> bool:
        return (
            PLATFORM_SUPERUSER_SCOPE in self.capabilities
            or "builder.read" in self.capabilities
        )

    @property
    def can_execute(self) -> bool:
        return (
            PLATFORM_SUPERUSER_SCOPE in self.capabilities
            or "builder.execute" in self.capabilities
        )

    @property
    def can_build_resources(self) -> bool:
        return any(
            capability.endswith(".readwrite") or capability.endswith(".execute")
            for tool in organization_builder_tool_names(self.capabilities)
            for capability in tool[1]
        )


@dataclass(frozen=True, slots=True)
class BuilderAuthorizationTargets:
    organizations: tuple[BuilderOrganizationAuthorization, ...]
    platform_capabilities: frozenset[str]
    managed_capabilities: frozenset[str]

    @property
    def is_platform_admin(self) -> bool:
        return PLATFORM_SUPERUSER_SCOPE in self.platform_capabilities

    @property
    def can_view_all(self) -> bool:
        return (
            PLATFORM_SUPERUSER_SCOPE in self.managed_capabilities
            or "builder.read" in self.managed_capabilities
        )

    @property
    def can_open_global_workspace(self) -> bool:
        capabilities = self.platform_capabilities
        return (
            PLATFORM_SUPERUSER_SCOPE in capabilities
            or {
                "builder.execute",
                "repository.readwrite",
            }.issubset(capabilities)
        )

    @property
    def has_builder_access(self) -> bool:
        return (
            self.can_open_global_workspace
            or self.can_view_all
            or any(target.can_read or target.can_execute for target in self.organizations)
        )


def organization_builder_tool_names(
    capabilities: frozenset[str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return registered organization-safe Builder tools allowed by capabilities."""

    from src.services.mcp_server.server import get_system_tools

    registered = {tool["id"] for tool in get_system_tools()}
    effective = implied_scopes(capabilities)
    return tuple(
        (operation.mcp.name, operation.action_scopes)
        for operation in OPERATION_CATALOG
        if operation.native_builder
        and operation.mcp is not None
        and operation.mcp.name in registered
        and operation.target_kind
        not in {OperationTargetKind.PLATFORM, OperationTargetKind.WORKSPACE}
        and not any(
            capability.startswith("repository.")
            for capability in operation.action_scopes
        )
        and (
            PLATFORM_SUPERUSER_SCOPE in effective
            or all(capability in effective for capability in operation.action_scopes)
        )
    )


def global_builder_tool_names(
    capabilities: frozenset[str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return registered Platform/Global Builder tools allowed by capabilities."""

    from src.services.mcp_server.server import get_system_tools

    registered = {tool["id"] for tool in get_system_tools()}
    effective = implied_scopes(capabilities)
    return tuple(
        (operation.mcp.name, operation.action_scopes)
        for operation in OPERATION_CATALOG
        if operation.native_builder
        and operation.mcp is not None
        and operation.mcp.name in registered
        and operation.target_kind is OperationTargetKind.PLATFORM
        and all(capability.endswith(".read") for capability in operation.action_scopes)
        and (
            PLATFORM_SUPERUSER_SCOPE in effective
            or all(capability in effective for capability in operation.action_scopes)
        )
    )


async def discover_builder_authorization_targets(
    db: AsyncSession,
    *,
    requester: UserPrincipal,
) -> BuilderAuthorizationTargets:
    """Resolve Builder discovery without pretending one boundary covers another.

    This is the entry-point exception to ordinary request-bound authorization:
    a person must be able to discover which exact boundaries they may select
    before the client can send ``X-Bifrost-Boundary``. Execution still resolves
    and rechecks one selected boundary through the central evaluator.
    """

    targets = await discover_authorization_targets(db, requester=requester)
    organization_targets: list[BuilderOrganizationAuthorization] = []
    for organization in targets.organizations:
        effective = organization.capabilities
        if not (
            PLATFORM_SUPERUSER_SCOPE in effective
            or "builder.read" in effective
            or "builder.execute" in effective
        ):
            continue
        organization_targets.append(
            BuilderOrganizationAuthorization(
                id=organization.id,
                name=organization.name,
                is_provider=organization.is_provider,
                capabilities=effective,
                role_ids=organization.role_ids,
            )
        )

    return BuilderAuthorizationTargets(
        organizations=tuple(organization_targets),
        platform_capabilities=targets.platform_capabilities,
        managed_capabilities=targets.managed_capabilities,
    )


__all__ = [
    "BuilderAuthorizationTargets",
    "BuilderOrganizationAuthorization",
    "discover_builder_authorization_targets",
    "global_builder_tool_names",
    "organization_builder_tool_names",
]
