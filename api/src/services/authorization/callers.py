"""Refresh database-backed authorization callers at sensitive operation boundaries."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from shared.role_cache import get_user_roles
from shared.system_account_guard import is_system_account
from src.core.principal import UserPrincipal
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.authorization.enforce import Caller, load_caller
from src.services.authorization.runtime_auth import load_runtime_principal


class CallerUnavailable(ValueError):
    """The caller no longer has a current authority source."""


async def load_human_caller(db: AsyncSession, user_id: UUID) -> Caller:
    """Load an active human account and its current role context."""
    if is_system_account(user_id):
        raise CallerUnavailable("The caller is no longer available")
    user = await db.get(User, user_id, populate_existing=True)
    if user is None or not user.is_active:
        raise CallerUnavailable("The caller is no longer available")

    organization = (
        None
        if user.organization_id is None
        else await db.get(Organization, user.organization_id, populate_existing=True)
    )
    role_ids, role_names = await get_user_roles(user.id, db)
    caller = await load_caller(
        db,
        UserPrincipal(
            user_id=user.id,
            email=user.email,
            name=user.name or "",
            organization_id=user.organization_id,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            is_verified=user.is_verified,
            is_external=user.is_external,
            is_provider_org=bool(organization and organization.is_provider),
            roles=role_names,
            role_ids=role_ids,
            role_names=role_names,
        ),
    )
    if caller.ctx is None:
        raise CallerUnavailable("The caller is no longer available")
    return caller


async def refresh_caller(db: AsyncSession, caller: Caller) -> Caller:
    """Refresh authority without accepting serialized roles or identity flags."""
    principal = caller.principal
    if principal.embed:
        raise CallerUnavailable("Embed callers cannot use this operation")

    runtime = principal.runtime_authority
    if runtime is None:
        if principal.engine_execution_id or principal.service_id or principal.service_attempt_id:
            raise CallerUnavailable("A typed runtime credential is required")
        return await load_human_caller(db, principal.user_id)

    refreshed_principal = await load_runtime_principal(
        db,
        {
            "credential_kind": runtime.kind,
            "sub": str(runtime.credential_id),
            "service_id": None if runtime.service_id is None else str(runtime.service_id),
        },
    )
    if refreshed_principal is None:
        raise CallerUnavailable("The runtime credential is no longer valid")
    refreshed = await load_caller(db, refreshed_principal)
    if refreshed.ctx is None:
        raise CallerUnavailable("The caller is no longer available")
    return refreshed
