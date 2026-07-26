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

from src.core.security import ACTOR_TYPE_SOLUTION_APP, decode_token

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


async def get_solution_app_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> SolutionAppPrincipal:
    """Authenticate a ``solution_app`` token, or 401.

    Rejects a normal user token: the absence of ``actor_type`` means the caller
    is a full user, and a full user's authority must not flow into the sealed
    app runtime.
    """
    if credentials is None:
        raise _UNAUTHENTICATED
    payload = decode_token(credentials.credentials, expected_type="access")
    if payload is None:
        raise _UNAUTHENTICATED

    if payload.get("actor_type") != ACTOR_TYPE_SOLUTION_APP:
        logger.warning(
            "Rejected token (actor_type=%s) on Solution app-host path %s",
            payload.get("actor_type"),
            request.url.path,
        )
        raise _UNAUTHENTICATED

    try:
        principal = SolutionAppPrincipal(
            actor_user_id=UUID(payload["actor_user_id"]),
            solution_id=UUID(payload["solution_id"]),
            app_id=UUID(payload["app_id"]),
            organization_id=UUID(payload["organization_id"]),
            jti=payload["jti"],
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Solution app token is missing or malformed in its binding claims")
        raise _UNAUTHENTICATED from None

    return principal


CurrentSolutionApp = Annotated[SolutionAppPrincipal, Depends(get_solution_app_principal)]
