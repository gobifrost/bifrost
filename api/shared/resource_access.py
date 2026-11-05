"""Ordinary read, inspection, and write decisions for Apps, Forms, and Agents."""

from __future__ import annotations

from enum import Enum
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.scope_resolver import UNSET, RequestedScope, shared_read_organization
from src.models.orm.agents import Agent, AgentRole
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.models.orm.forms import Form, FormRole
from src.models.orm.users import UserRole
from src.services.authorization.enforce import Caller, allows_permission, user_reach


class ResourceKind(str, Enum):
    APP = "app"
    FORM = "form"
    AGENT = "agent"


Resource = Application | Form | Agent
T = TypeVar("T", bound=Resource)


def _access_level(resource: Resource) -> str | None:
    level = resource.access_level
    return level.value if isinstance(level, Enum) else level


def _prefix(kind: ResourceKind) -> str:
    return f"{kind.value}s"


def _original_user_id(caller: Caller) -> UUID | None:
    return None if caller.ctx is None else caller.ctx.user_id


def _owner_user_id(resource: Resource) -> UUID | None:
    return getattr(resource, "owner_user_id", None)


def can_write_resource(kind: ResourceKind, caller: Caller, resource: Resource) -> bool:
    """Whether the original caller may write this persisted resource."""
    organization_id = resource.organization_id
    if not user_reach(caller).covers(organization_id):
        return False
    if kind is ResourceKind.AGENT and _access_level(resource) == "private":
        owner_user_id = _owner_user_id(resource)
        if owner_user_id == _original_user_id(caller) and owner_user_id is not None:
            return True
        return allows_permission(caller, "agents.readwrite.all", organization_id)
    return allows_permission(caller, f"{_prefix(kind)}.readwrite", organization_id)


class ResourceAccessResolver:
    """Request-local ordinary resource access decisions.

    Named permission checks are evaluated from the caller's original context.
    Role membership is loaded lazily and retained only for this resolver
    instance, so a list can share the lookup without creating cross-request
    authorization state.
    """

    def __init__(self, session: AsyncSession, caller: Caller) -> None:
        self.session = session
        self.caller = caller
        self._role_ids: list[UUID] | None = None

    async def can_read(
        self,
        kind: ResourceKind,
        resource: Resource,
        *,
        selected_organization_id: RequestedScope = UNSET,
    ) -> bool:
        """Whether the caller may use a reachable resource's ordinary read path."""
        organization_id = resource.organization_id
        if not user_reach(self.caller).covers(organization_id):
            return False

        if kind is ResourceKind.AGENT and _access_level(resource) == "private":
            if (
                _owner_user_id(resource) == _original_user_id(self.caller)
                and _owner_user_id(resource) is not None
            ):
                return True
            return allows_permission(self.caller, "agents.read.all", organization_id)

        permission_org = shared_read_organization(
            self.caller,
            organization_id,
            selected_organization_id=selected_organization_id,
        )
        prefix = _prefix(kind)
        if kind is ResourceKind.APP:
            if not allows_permission(self.caller, "apps.readbasic", permission_org):
                return False
            return (
                await self._has_ordinary_access(kind, resource)
                or allows_permission(self.caller, "apps.read", organization_id)
                or allows_permission(self.caller, "apps.readwrite", organization_id)
            )

        if allows_permission(self.caller, f"{prefix}.read", permission_org):
            return True
        return allows_permission(
            self.caller, f"{prefix}.readbasic", permission_org
        ) and await self._has_ordinary_access(kind, resource)

    async def filter_readable(
        self,
        kind: ResourceKind,
        resources: list[T],
        *,
        selected_organization_id: RequestedScope = UNSET,
    ) -> list[T]:
        """Keep precisely the resources accepted by :meth:`can_read`."""
        return [
            resource
            for resource in resources
            if await self.can_read(
                kind,
                resource,
                selected_organization_id=selected_organization_id,
            )
        ]

    def can_inspect(self, kind: ResourceKind, resource: Resource) -> bool:
        """Whether a management reader may inspect this persisted resource."""
        organization_id = resource.organization_id
        if not user_reach(self.caller).covers(organization_id):
            return False
        if kind is ResourceKind.AGENT and _access_level(resource) == "private":
            return (
                _owner_user_id(resource) == _original_user_id(self.caller)
                and _owner_user_id(resource) is not None
            ) or allows_permission(self.caller, "agents.read.all", organization_id)
        return allows_permission(self.caller, f"{_prefix(kind)}.read", organization_id)

    def can_write(self, kind: ResourceKind, resource: Resource) -> bool:
        return can_write_resource(kind, self.caller, resource)

    async def _has_ordinary_access(
        self, kind: ResourceKind, resource: Resource
    ) -> bool:
        level = _access_level(resource)
        if level == "everyone":
            return True
        if level == "authenticated":
            return self.caller.ctx is not None and not self.caller.ctx.is_external
        if level != "role_based":
            return False
        return await self._has_matching_role(kind, resource)

    async def _has_matching_role(
        self, kind: ResourceKind, resource: Resource
    ) -> bool:
        user_id = _original_user_id(self.caller)
        if user_id is None:
            return False
        if self._role_ids is None:
            self._role_ids = list(
                (
                    await self.session.execute(
                        select(UserRole.role_id).where(UserRole.user_id == user_id)
                    )
                ).scalars().all()
            )
        if not self._role_ids:
            return False
        role_model, resource_column = {
            ResourceKind.APP: (AppRole, AppRole.app_id),
            ResourceKind.FORM: (FormRole, FormRole.form_id),
            ResourceKind.AGENT: (AgentRole, AgentRole.agent_id),
        }[kind]
        return (
            await self.session.execute(
                select(role_model.role_id)
                .where(
                    resource_column == resource.id,
                    role_model.role_id.in_(self._role_ids),
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
