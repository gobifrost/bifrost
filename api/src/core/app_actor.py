"""Authentication for ``solution_app`` actor tokens (builder app host).

Every normal user-auth path default-denies a token carrying ``actor_type``
(see ``src.core.security.is_actor_token``). This module is the single explicit
opt-in on the other side of that fence: it authenticates *only*
``actor_type="solution_app"`` tokens and *only* for app-host routes.

The rejection matrix is deliberately symmetric — one direction each way:

===========================  =========================  ======================
Token                        Normal user route          App-host route
===========================  =========================  ======================
Normal user access token     authenticates              401 (here)
``solution_app`` token       401 (``is_actor_token``)   authenticates (here)
===========================  =========================  ======================

The principal's ``solution_id``/``app_id``/``organization_id`` come from the
signed token and nowhere else. A client-supplied header, query parameter, or
body field claiming a different Solution or organization is ignored, because
the app is untrusted generated code running in the user's browser and could
set any of them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authorization_scopes import (
    PLATFORM_SUPERUSER_SCOPE,
    SOLUTION_APP_RUNTIME_SCOPES,
    validate_role_scopes,
)
from src.core.database import get_db
from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.core.security import ACTOR_TYPE_SOLUTION_APP, decode_token
from src.services.solutions.access import VISIBILITY_PRIVATE

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="A Solution app token is required",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class SolutionAppPrincipal:
    """The authenticated caller behind a ``solution_app`` token.

    Frozen: nothing downstream may widen the bound Solution, app, or
    organization after authentication.
    """

    actor_user_id: UUID
    solution_id: UUID
    app_id: UUID
    organization_id: UUID
    jti: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        """Whether this attenuated actor token carries an exact action scope."""

        return scope in self.scopes


async def get_solution_app_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SolutionAppPrincipal:
    """Authenticate a ``solution_app`` token, or 401.

    Rejects a normal user token: the absence of ``actor_type`` means the caller
    is a full user, and a full user's authority must not flow into the sealed
    app runtime.
    """
    if credentials is None:
        raise _UNAUTHENTICATED
    return await authenticate_solution_app_token(
        credentials.credentials,
        db,
        request_path=request.url.path,
    )


async def authenticate_solution_app_token(
    token: str,
    db: AsyncSession,
    *,
    request_path: str,
) -> SolutionAppPrincipal:
    """Authenticate an actor token against its live private Solution binding.

    HTTP and WebSocket app-host entry points share this function so they apply
    exactly the same token-type fence and immediate-revocation checks.
    """
    payload = decode_token(token, expected_type="access")
    if payload is None:
        raise _UNAUTHENTICATED

    if payload.get("actor_type") != ACTOR_TYPE_SOLUTION_APP:
        logger.warning(
            "Rejected token (actor_type=%s) on Solution app-host path %s",
            payload.get("actor_type"),
            request_path,
        )
        raise _UNAUTHENTICATED

    try:
        scopes = frozenset(
            validate_role_scopes(payload["scopes"], custom_role=False)
        )
        if (
            PLATFORM_SUPERUSER_SCOPE in scopes
            or not scopes
            or not scopes.issubset(SOLUTION_APP_RUNTIME_SCOPES)
        ):
            raise ValueError("invalid Solution app scope set")
        principal = SolutionAppPrincipal(
            actor_user_id=UUID(payload["actor_user_id"]),
            solution_id=UUID(payload["solution_id"]),
            app_id=UUID(payload["app_id"]),
            organization_id=UUID(payload["organization_id"]),
            jti=payload["jti"],
            scopes=scopes,
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Solution app token is missing or malformed in its binding claims")
        raise _UNAUTHENTICATED from None

    # Recompute ownership and app binding from live server state on every
    # request. Promotion, owner deletion, app removal, or Solution deactivation
    # therefore revokes a still-unexpired token immediately; no boolean claim
    # supplied by generated code is trusted as an owner bypass.
    authorized = (
        await db.execute(
            select(Solution.id)
            .join(
                Application,
                Application.solution_id == Solution.id,
            )
            .where(
                Solution.id == principal.solution_id,
                Solution.visibility == VISIBILITY_PRIVATE,
                Solution.status == "active",
                Solution.owner_user_id == principal.actor_user_id,
                Solution.organization_id == principal.organization_id,
                Application.id == principal.app_id,
                Application.organization_id == principal.organization_id,
            )
        )
    ).scalar_one_or_none()
    if authorized is None:
        raise _UNAUTHENTICATED

    return principal


CurrentSolutionApp = Annotated[SolutionAppPrincipal, Depends(get_solution_app_principal)]
