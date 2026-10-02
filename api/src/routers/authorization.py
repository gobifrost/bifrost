"""
Authorization Router

The signed-in user's own authorization summary, for the UI to decide which
controls to show. Every request is still decided by the server.
"""

from fastapi import APIRouter, HTTPException, status

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.role_assignments import AuthorizationSummary
from src.services.user_role_assignments import authorization_summary

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get(
    "/authorization",
    response_model=AuthorizationSummary,
    summary="Get my authorization",
    description=(
        "Whether the signed-in user is a Platform Admin, their base role, and the "
        "permissions their roles grant at each boundary. Read from the database on "
        "every call."
    ),
)
async def get_my_authorization(
    user: CurrentActiveUser,
    db: DbSession,
) -> AuthorizationSummary:
    if user.embed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not available to embed sessions")
    return await authorization_summary(db, user.user_id)
