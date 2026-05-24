"""
Authentication and Authorization

Provides FastAPI dependencies for authentication and authorization.
Supports JWT bearer token authentication with user context injection.
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.database import DbSession
from src.core.security import decode_token
from shared.role_cache import get_user_roles

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# HTTP Bearer token scheme
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class UserPrincipal:
    """
    Authenticated user principal.

    Represents an authenticated user with their identity and permissions.
    All user info is extracted from JWT claims (no database lookup required).

    Auth model:
    - is_superuser=true, org_id=UUID: Platform admin in an org
    - is_superuser=false, org_id=UUID: Regular org user
    - is_superuser=true, org_id=None: System account (global scope)
    - is_superuser=false, org_id=None: INVALID (rejected at token parsing)
    """
    user_id: UUID
    email: str
    organization_id: UUID | None  # User's org (None for system accounts)
    name: str = ""
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False
    roles: list[str] = field(default_factory=list)
    # Role identity used by table-policy `has_role` evaluator. Populated by
    # `get_execution_context` from the `user_roles` table; empty for token-only
    # principals (e.g. system accounts) and embed sessions.
    role_ids: list[UUID] = field(default_factory=list)
    role_names: list[str] = field(default_factory=list)
    embed: bool = False  # True for embed session tokens (scoped to app_id)
    jti: str | None = None  # JWT ID for embed tokens (used for execution scoping)
    app_id: str | None = None  # App ID for embed tokens
    form_id: str | None = None  # Form ID for form embed tokens
    verified_params: dict[str, str] | None = None  # HMAC-verified query params

    @property
    def is_platform_admin(self) -> bool:
        """Check if user is a platform admin (superuser)."""
        return self.is_superuser

    @property
    def is_system_account(self) -> bool:
        """Check if this is a system account (superuser with no org)."""
        return self.is_superuser and self.organization_id is None

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role."""
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        """Check if user has any of the specified roles."""
        return any(role in self.roles for role in roles)


@dataclass
class ExecutionContext:
    """
    Execution context for request handling.

    Contains the authenticated user, organization scope, and database session.

    Scope rules:
    - Regular users: org_id = user's home organization (always set)
    - System user: org_id = workflow's organization (None for global workflows)

    This ensures org-scoped workflows only access their org's data,
    even when triggered by schedules or webhooks.
    """
    user: UserPrincipal
    org_id: UUID | None  # Execution scope (None only for system user + global workflow)
    db: "AsyncSession"

    @property
    def scope(self) -> str:
        """Get the scope string for data access (org_id or 'GLOBAL')."""
        return str(self.org_id) if self.org_id else "GLOBAL"

    @property
    def user_id(self) -> str:
        """Get user ID as string."""
        return str(self.user.user_id)

    @property
    def is_global_scope(self) -> bool:
        """Check if operating in global scope (system user + global workflow only)."""
        return self.org_id is None

    @property
    def is_platform_admin(self) -> bool:
        """Check if user is a platform admin (superuser)."""
        return self.user.is_platform_admin


async def get_current_user_optional(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> UserPrincipal | None:
    """
    Get the current user from JWT token (optional).

    Checks for authentication in this order:
    1. Authorization: Bearer header (for API clients/service-to-service)
    2. access_token cookie (for browser clients)

    User info is extracted from JWT claims. Tokens must:
    - Have type="access" (refresh tokens are rejected)
    - Have valid issuer and audience claims
    - Include email claim
    - Have org_id if not a superuser

    Returns None if no token is provided or token is invalid.
    Does not raise an exception for unauthenticated requests.

    Args:
        request: FastAPI request object
        credentials: HTTP Bearer credentials from request
        db: Database session (unused, kept for signature compatibility)

    Returns:
        UserPrincipal if authenticated, None otherwise
    """
    token = None
    token_type = "access"

    # Try Authorization header first (API clients)
    if credentials:
        token = credentials.credentials
    # Fall back to cookie (browser clients)
    elif "access_token" in request.cookies:
        token = request.cookies["access_token"]
    # Fall back to embed_token cookie (iframe embed sessions)
    elif "embed_token" in request.cookies:
        token = request.cookies["embed_token"]
        token_type = "embed"

    if not token:
        return None

    # Decode and validate token
    payload = decode_token(token, expected_type=token_type)

    if payload is None:
        return None

    if payload.get("mcp"):
        logger.warning("Rejecting MCP-scoped bearer token on REST auth path")
        return None

    # Extract user ID from token
    user_id_str = payload.get("sub")
    if not user_id_str:
        return None

    try:
        user_id = UUID(user_id_str)
    except ValueError:
        return None

    # Token MUST have email claim
    if "email" not in payload:
        logger.warning(
            f"Token for user {user_id} missing required email claim. "
            "Legacy tokens are no longer supported."
        )
        return None

    is_superuser = payload.get("is_superuser", False)
    org_id_str = payload.get("org_id")

    # Parse organization_id
    organization_id: UUID | None = None
    if org_id_str:
        try:
            organization_id = UUID(org_id_str)
        except ValueError:
            logger.warning(f"Token for user {user_id} has invalid org_id format: {org_id_str}")
            return None
    elif not is_superuser and not payload.get("embed", False):
        # Non-superuser without org is invalid (embed tokens are exempt —
        # they may reference forms/apps in orgs without the token holder
        # being a member, since access was already verified via HMAC)
        logger.error(
            f"Invalid token for user {user_id}: "
            "non-superuser must have organization_id"
        )
        return None
    # else: superuser with no org = system account (valid)
    # else: embed token without org = valid (HMAC-verified)

    return UserPrincipal(
        user_id=user_id,
        email=payload.get("email", ""),
        organization_id=organization_id,
        name=payload.get("name", ""),
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        roles=payload.get("roles", []),
        embed=payload.get("embed", False),
        jti=payload.get("jti"),
        app_id=payload.get("app_id"),
        form_id=payload.get("form_id"),
        verified_params=payload.get("verified_params"),
    )


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> UserPrincipal:
    """
    Get the current user from JWT token (required).

    Raises HTTPException if not authenticated.

    Args:
        request: FastAPI request object
        credentials: HTTP Bearer credentials from request
        db: Database session

    Returns:
        UserPrincipal for authenticated user

    Raises:
        HTTPException: If not authenticated or token is invalid
    """
    user = await get_current_user_optional(request, credentials, db)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_active_user(
    user: Annotated[UserPrincipal, Depends(get_current_user)],
) -> UserPrincipal:
    """
    Get the current active user.

    Raises HTTPException if user is inactive.

    Args:
        user: Current user from authentication

    Returns:
        UserPrincipal for active user

    Raises:
        HTTPException: If user is inactive
    """
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    return user


async def get_current_superuser(
    user: Annotated[UserPrincipal, Depends(get_current_active_user)],
) -> UserPrincipal:
    """
    Get the current superuser (platform admin).

    Raises HTTPException if user is not a superuser.

    Args:
        user: Current active user

    Returns:
        UserPrincipal for superuser

    Raises:
        HTTPException: If user is not a superuser
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser privileges required"
        )
    return user


# Dependency for requiring platform admin access
# Usage: dependencies=[RequirePlatformAdmin]
RequirePlatformAdmin = Depends(get_current_superuser)


async def get_execution_context(
    user: Annotated[UserPrincipal, Depends(get_current_active_user)],
    db: DbSession,
) -> ExecutionContext:
    """
    Get execution context for HTTP requests.

    For regular users (including platform admins), org_id is always their
    home organization. All users are treated the same for data access -
    they see global resources + their organization's resources.

    Admin-only capabilities (org management, user management) are controlled
    by endpoint-level authorization, not by ExecutionContext scope.

    Note: For system user executions (schedules, webhooks), use
    create_system_execution_context() which sets org_id from the workflow.

    Args:
        user: Current active user
        db: Database session

    Returns:
        ExecutionContext with user and organization scope
    """
    # Populate role_ids / role_names. The table-policy evaluator's
    # `has_role` function reads these (the JWT claims do not carry the post-
    # token role assignments needed by row-level policies). Reads go through
    # the per-user role cache (Redis); DB is fallback on miss.
    if not user.role_ids and not user.role_names:
        role_ids, role_names = await get_user_roles(user.user_id, db)
        user.role_ids = role_ids
        user.role_names = role_names

    return ExecutionContext(
        user=user,
        org_id=user.organization_id,
        db=db,
    )


# Type aliases for dependency injection
CurrentUser = Annotated[UserPrincipal, Depends(get_current_user)]
CurrentActiveUser = Annotated[UserPrincipal, Depends(get_current_active_user)]
CurrentSuperuser = Annotated[UserPrincipal, Depends(get_current_superuser)]
Context = Annotated[ExecutionContext, Depends(get_execution_context)]




async def get_current_user_from_db(
    current_user: UserPrincipal,
    db,  # DbSession - avoid circular import
):
    """
    Get the actual User model from database.

    This is needed when you need to access user relationships (MFA methods, etc.)
    that aren't available on the UserPrincipal dataclass.

    Args:
        current_user: UserPrincipal from JWT
        db: Database session

    Returns:
        User model from database

    Raises:
        HTTPException: If user not found
    """
    from src.repositories.users import UserRepository

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(current_user.user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user


async def get_current_user_ws(websocket) -> UserPrincipal | None:
    """
    Get current user from WebSocket connection.

    Checks for token in this order:
    1. Cookie: access_token (for browser clients - most common)
    2. Authorization header (for service clients)

    Note: Query parameter tokens are NOT supported for security reasons
    (URLs may be logged by proxies/servers).

    Args:
        websocket: FastAPI WebSocket connection

    Returns:
        UserPrincipal if authenticated, None otherwise
    """
    token = None

    # Try cookie first (browser clients)
    if "access_token" in websocket.cookies:
        token = websocket.cookies["access_token"]

    # Try Authorization header (some WebSocket clients support this)
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]

    # Try query parameter (for embed tokens — browser WebSocket API can't set headers)
    if not token:
        token = websocket.query_params.get("token")

    if not token:
        return None

    # Decode and validate token - must be an access token
    payload = decode_token(token, expected_type="access")
    if payload is None:
        return None
    if payload.get("mcp"):
        logger.warning("Rejecting MCP-scoped bearer token on WebSocket auth path")
        return None

    user_id_str = payload.get("sub")
    if not user_id_str:
        return None

    try:
        user_id = UUID(user_id_str)
    except ValueError:
        return None

    # Token MUST have email claim
    if "email" not in payload:
        logger.warning(
            f"WebSocket token for user {user_id} missing required email claim. "
            "Legacy tokens are no longer supported."
        )
        return None

    is_superuser = payload.get("is_superuser", False)
    org_id_str = payload.get("org_id")

    # Parse organization_id
    organization_id: UUID | None = None
    if org_id_str:
        try:
            organization_id = UUID(org_id_str)
        except ValueError:
            logger.warning(f"WebSocket token for user {user_id} has invalid org_id format: {org_id_str}")
            return None
    elif not is_superuser and not payload.get("embed", False):
        # Non-superuser without org is invalid (embed tokens exempt)
        logger.error(
            f"Invalid WebSocket token for user {user_id}: "
            "non-superuser must have organization_id"
        )
        return None
    # else: superuser with no org = system account (valid)
    # else: embed token without org = valid (HMAC-verified)

    return UserPrincipal(
        user_id=user_id,
        email=payload.get("email", ""),
        organization_id=organization_id,
        name=payload.get("name", ""),
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        roles=payload.get("roles", []),
        embed=payload.get("embed", False),
        jti=payload.get("jti"),
        app_id=payload.get("app_id"),
        form_id=payload.get("form_id"),
        verified_params=payload.get("verified_params"),
    )
