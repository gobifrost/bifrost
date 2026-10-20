"""Current original-user authority for Solution records and operations."""

from __future__ import annotations

from uuid import UUID
from pathlib import Path
import zipfile

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.orm.solutions import Solution
from src.services.authorization.enforce import Caller, load_caller, require_permission, user_reach


def original_organization(caller: Caller) -> UUID | None:
    runtime = caller.principal.runtime_authority
    if runtime is not None:
        return runtime.organization_id
    return None if caller.ctx is None else caller.ctx.home_organization_id


def original_user_id(caller: Caller) -> UUID:
    if caller.ctx is None:
        raise HTTPException(403, "The caller is no longer available")
    return caller.ctx.user_id


def authorize_backup_archive(caller: Caller, path: Path, organization_id: UUID | None) -> None:
    """Refuse encrypted backup material before any password/decrypt check."""
    with zipfile.ZipFile(path) as archive:
        if ".bifrost/secrets.enc" in archive.namelist():
            require_permission(caller, "secrets.read", organization_id)


async def authorize_scope(
    db: AsyncSession,
    principal: UserPrincipal,
    organization_id: UUID | None,
    permission: str,
) -> Caller:
    caller = await load_caller(db, principal)
    require_permission(caller, permission, organization_id)
    if not user_reach(caller).covers(organization_id):
        raise HTTPException(403, "The organization is outside your access")
    return caller


async def authorize_solution(
    db: AsyncSession,
    principal: UserPrincipal,
    solution_id: UUID,
    permission: str,
) -> Caller:
    caller = await load_caller(db, principal)
    solution = await db.get(Solution, solution_id)
    if solution is None or not user_reach(caller).covers(solution.organization_id):
        raise HTTPException(404, "Solution not found")
    require_permission(caller, permission, solution.organization_id)
    return caller
