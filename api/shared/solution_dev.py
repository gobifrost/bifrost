"""Server-side capability for ``bifrost solution start`` local previews."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import jwt
from fastapi import HTTPException, status

from shared.models import SolutionDevContextResponse
from src.config import get_settings
from src.core.principal import UserPrincipal
from src.core.security import decode_token
from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.authorization.enforce import (
    PermissionDenied,
    load_caller,
    require_named_permission,
    user_reach,
)
from src.services.solutions.deploy import solution_entity_id

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


DEV_CONTEXT_HEADER = "X-Bifrost-Dev-Context"
DEV_CONTEXT_TYPE = "solution_dev"
DEV_CONTEXT_LIFETIME = timedelta(minutes=10)
INVALID_DEV_CONTEXT_DETAIL = "Invalid solution dev context"


@dataclass(frozen=True)
class ValidatedSolutionDevContext:
    solution_id: UUID
    organization_id: UUID | None
    app_id: UUID


def _invalid_dev_context(status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail=INVALID_DEV_CONTEXT_DETAIL)


async def require_solution_developer(
    db: "AsyncSession", user: UserPrincipal, organization_id: UUID | None
) -> None:
    """Admit an active internal human with Solutions write at an install.

    Local development carries a subject-bound install capability, never a
    data-plane permission. Runtime powers, service credentials, and effects
    actors therefore cannot obtain or renew it.
    """
    if (
        user.embed
        or user.runtime_authority is not None
        or user.engine_execution_id
        or user.service_id
    ):
        raise _invalid_dev_context(status.HTTP_403_FORBIDDEN)
    current_user = await db.get(User, user.user_id)
    if current_user is None or not current_user.is_active or current_user.is_external:
        raise _invalid_dev_context(status.HTTP_403_FORBIDDEN)
    caller = await load_caller(db, user)
    if caller.ctx is None or caller.ctx.is_external or not user_reach(caller).covers(organization_id):
        raise _invalid_dev_context(status.HTTP_403_FORBIDDEN)
    try:
        require_named_permission(caller, "solutions.readwrite", organization_id)
    except PermissionDenied:
        raise _invalid_dev_context(status.HTTP_403_FORBIDDEN) from None


async def resolve_solution_dev_app_id(
    db: "AsyncSession", solution_id: UUID, manifest_app_id: UUID
) -> UUID:
    """Resolve an app manifest identity without creating an Application row.

    Fresh local source names an app by its portable manifest UUID, while an
    install uses the deterministic UUID5 remap.  A captured/pulled manifest can
    already carry its installed UUID, which is accepted only after confirming
    it belongs to this install.  A source UUID owned by another install (or by
    ``_repo``) is still portable source data, so it remaps into this install.
    """
    # Match deploy's owner-first identity rule for captured entities.
    direct_row = await db.get(Application, manifest_app_id)
    if direct_row is not None and direct_row.solution_id == solution_id:
        return manifest_app_id

    installed_id = solution_entity_id(solution_id, manifest_app_id)
    installed_row = await db.get(Application, installed_id)
    if installed_row is not None:
        if installed_row.solution_id != solution_id:
            raise _invalid_dev_context(status.HTTP_403_FORBIDDEN)
        return installed_id

    # The source may be local and never deployed.  Its deterministic installed
    # identity is still sufficient to bind data-plane calls; do not mutate DB.
    return installed_id


def _mint_solution_dev_token(
    *, user_id: UUID, solution_id: UUID, organization_id: UUID | None, app_id: UUID,
    expires_delta: timedelta,
) -> tuple[str, float]:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + expires_delta
    claims = {
        "sub": str(user_id),
        "solution_id": str(solution_id),
        "organization_id": str(organization_id) if organization_id is not None else None,
        "app_id": str(app_id),
        "exp": expires_at,
        "type": DEV_CONTEXT_TYPE,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return (
        jwt.encode(claims, settings.secret_key, algorithm=settings.algorithm),
        expires_at.timestamp(),
    )


async def issue_solution_dev_context(
    db: "AsyncSession",
    user: UserPrincipal,
    solution_id: UUID,
    manifest_app_id: UUID,
    *,
    expires_delta: timedelta = DEV_CONTEXT_LIFETIME,
) -> SolutionDevContextResponse:
    """Mint a short-lived local-preview context for an active install."""
    solution = await db.get(Solution, solution_id)
    if solution is None or solution.status != "active":
        raise _invalid_dev_context(status.HTTP_403_FORBIDDEN)
    await require_solution_developer(db, user, solution.organization_id)
    app_id = await resolve_solution_dev_app_id(db, solution.id, manifest_app_id)
    token, expires_at = _mint_solution_dev_token(
        user_id=user.user_id,
        solution_id=solution.id,
        organization_id=solution.organization_id,
        app_id=app_id,
        expires_delta=expires_delta,
    )
    return SolutionDevContextResponse(
        token=token,
        expires_at=expires_at,
        solution_id=solution.id,
        organization_id=solution.organization_id,
        app_id=app_id,
    )


def _uuid_claim(payload: dict, name: str, *, nullable: bool = False) -> UUID | None:
    value = payload.get(name)
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError(name)
    return UUID(value)


async def validate_solution_dev_context(
    db: "AsyncSession", user: UserPrincipal, token: str
) -> ValidatedSolutionDevContext:
    """Verify a supplemental dev header against current identity and install state."""
    payload = decode_token(token, expected_type=DEV_CONTEXT_TYPE)
    if payload is None:
        raise _invalid_dev_context(status.HTTP_401_UNAUTHORIZED)
    try:
        subject = _uuid_claim(payload, "sub")
        solution_id = _uuid_claim(payload, "solution_id")
        organization_id = _uuid_claim(payload, "organization_id", nullable=True)
        app_id = _uuid_claim(payload, "app_id")
    except (TypeError, ValueError):
        raise _invalid_dev_context(status.HTTP_401_UNAUTHORIZED) from None
    assert subject is not None and solution_id is not None and app_id is not None
    if subject != user.user_id:
        raise _invalid_dev_context(status.HTTP_403_FORBIDDEN)
    solution = await db.get(Solution, solution_id)
    if (
        solution is None
        or solution.status != "active"
        or solution.organization_id != organization_id
    ):
        raise _invalid_dev_context(status.HTTP_403_FORBIDDEN)
    await require_solution_developer(db, user, solution.organization_id)
    # A local, never-deployed app has no row.  Once a row exists, ownership
    # must still match the signed install (including after a deploy/move).
    app = await db.get(Application, app_id)
    if app is not None and app.solution_id != solution_id:
        raise _invalid_dev_context(status.HTTP_403_FORBIDDEN)
    return ValidatedSolutionDevContext(
        solution_id=solution_id,
        organization_id=organization_id,
        app_id=app_id,
    )
