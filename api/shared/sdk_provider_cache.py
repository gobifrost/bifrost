"""Server-owned cache scope for data-provider workflow children."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import (
    ProviderCacheReadRequest,
    ProviderCacheReadResponse,
    ProviderCacheWriteRequest,
    ProviderCacheWriteResponse,
)
from src.core.cache.data_provider_cache import cache_result, get_cached_result
from src.core.principal import UserPrincipal
from src.models.orm.workflows import Workflow
from src.services.authorization.explain import Powers, RunUser, load_powers, load_run_user

if TYPE_CHECKING:
    from src.services.authorization.runtime import RuntimeAuthority


class ProviderCacheScopeError(ValueError):
    """The current server facts cannot authorize a provider-cache partition."""


@dataclass(frozen=True)
class ProviderCacheScope:
    organization_id: UUID | None
    workflow_id: UUID
    authority_fingerprint: str
    ttl_seconds: int


def provider_cache_authority_fingerprint(
    run_user: RunUser,
    acting_user_id: UUID,
    powers: Powers,
    organization_id: UUID | None,
) -> str:
    """Hash the complete current authority that may affect provider output."""
    role_grants = sorted(
        (
            str(grant.role_id),
            sorted(grant.permissions),
            sorted(
                (str(boundary.kind), None if boundary.organization_id is None else str(boundary.organization_id))
                for boundary in grant.boundaries
            ),
        )
        for grant in run_user.ctx.role_grants
    )
    payload = {
        "run_user_id": str(run_user.user_id),
        "acting_user_id": str(acting_user_id),
        "organization_id": None if organization_id is None else str(organization_id),
        "run_user": {
            "home_organization_id": (
                None if run_user.ctx.home_organization_id is None else str(run_user.ctx.home_organization_id)
            ),
            "base_role_id": str(run_user.ctx.base_role_id),
            "base_permissions": sorted(run_user.ctx.base_permissions),
            "is_external": run_user.ctx.is_external,
            "identity_kind": run_user.identity_kind,
            "is_active": run_user.is_active,
            "role_grants": role_grants,
        },
        "powers": {
            "mode": str(powers.mode),
            "grants": sorted(grant.permission for grant in powers.grants),
        },
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:32]


async def build_provider_cache_scope(
    db: AsyncSession,
    *,
    workflow: Workflow,
    organization_id: UUID | None,
    run_user_id: UUID | None,
    acting_user_id: UUID | None,
) -> ProviderCacheScope:
    """Build a cache scope from durable facts, never execution request fields."""
    current_workflow = await db.get(Workflow, workflow.id, populate_existing=True)
    if current_workflow is None:
        raise ProviderCacheScopeError("Provider cache workflow no longer exists.")
    workflow = current_workflow
    if workflow.type != "data_provider":
        raise ProviderCacheScopeError("Only data-provider workflows may use the provider cache.")
    if run_user_id is None or acting_user_id is None:
        raise ProviderCacheScopeError("Provider cache requires persisted run-user and actor lineage.")
    run_user = await load_run_user(db, run_user_id)
    if run_user is None or await load_run_user(db, acting_user_id) is None:
        raise ProviderCacheScopeError("Provider cache lineage no longer names current users.")
    powers = await load_powers(db, workflow.id)
    return ProviderCacheScope(
        organization_id=organization_id,
        workflow_id=workflow.id,
        authority_fingerprint=provider_cache_authority_fingerprint(
            run_user, acting_user_id, powers, organization_id
        ),
        ttl_seconds=max(0, workflow.cache_ttl_seconds or 0),
    )


async def _runtime_scope(db: AsyncSession, user: UserPrincipal) -> ProviderCacheScope:
    authority: RuntimeAuthority | None = user.runtime_authority
    if authority is None or authority.kind != "workflow_execution" or authority.workflow_id is None:
        raise HTTPException(403, "A live data-provider workflow credential is required")
    workflow = await db.get(Workflow, authority.workflow_id)
    if workflow is None:
        raise HTTPException(403, "Runtime workflow no longer exists")
    try:
        return await build_provider_cache_scope(
            db,
            workflow=workflow,
            organization_id=authority.organization_id,
            run_user_id=authority.run_user.user_id,
            acting_user_id=authority.acting_user_id,
        )
    except ProviderCacheScopeError as exc:
        raise HTTPException(403, str(exc)) from exc


async def read_provider_cache(
    db: AsyncSession,
    user: UserPrincipal,
    request: ProviderCacheReadRequest,
) -> ProviderCacheReadResponse:
    scope = await _runtime_scope(db, user)
    if scope.ttl_seconds <= 0:
        return ProviderCacheReadResponse(hit=False)
    entry = await get_cached_result(
        None if scope.organization_id is None else str(scope.organization_id),
        str(scope.workflow_id),
        scope.authority_fingerprint,
        request.parameters,
    )
    if entry is None:
        return ProviderCacheReadResponse(hit=False)
    return ProviderCacheReadResponse(
        hit=True,
        result=entry.get("data"),
        expires_at=entry.get("expires_at"),
    )


async def write_provider_cache(
    db: AsyncSession,
    user: UserPrincipal,
    request: ProviderCacheWriteRequest,
) -> ProviderCacheWriteResponse:
    scope = await _runtime_scope(db, user)
    if scope.ttl_seconds <= 0:
        return ProviderCacheWriteResponse()
    expires_at: datetime = await cache_result(
        None if scope.organization_id is None else str(scope.organization_id),
        str(scope.workflow_id),
        scope.authority_fingerprint,
        request.parameters,
        request.result,
        scope.ttl_seconds,
    )
    return ProviderCacheWriteResponse(expires_at=expires_at)
