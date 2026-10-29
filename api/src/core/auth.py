"""
Authentication and Authorization

Provides FastAPI dependencies for authentication and authorization.
Supports JWT bearer token authentication with user context injection.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.db_deps import DbSession
from src.core.principal import UserPrincipal
from src.core.security import decode_token
from shared.browser_impersonation import (
    BrowserImpersonationError,
    IMPERSONATION_COOKIE_NAME,
    validate_browser_impersonation,
)
from src.services.authorization.runtime_auth import (
    is_runtime_credential_payload,
    load_runtime_principal,
)
from shared.role_cache import get_user_roles

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# HTTP Bearer token scheme
bearer_scheme = HTTPBearer(auto_error=False)


def select_http_auth_token(
    request: Request,
    bearer_token: str | None,
    *,
    allow_impersonation_cookie: bool = True,
) -> str | None:
    """Resolve HTTP credentials with browser impersonation precedence.

    An active marker deliberately wins over ordinary human bearer and access
    cookies. Typed runtime and embed credentials retain their own authority and
    never become impersonated browser traffic.
    """
    token = (
        bearer_token
        or request.cookies.get("access_token")
        or request.cookies.get("embed_token")
    )
    marker = request.cookies.get(IMPERSONATION_COOKIE_NAME)
    if allow_impersonation_cookie and marker:
        payload = decode_token(token, expected_type="access") if token else None
        if payload and (is_runtime_credential_payload(payload) or payload.get("embed")):
            return token
        return marker
    return token


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
    # The calling Solution app's id (from the X-Bifrost-App header set by the v2
    # SDK provider). Lets name-based table resolution scope to the app's OWN
    # install so a `useTable("name")` reaches its deployed table, not a sibling
    # install's at the same name (Codex #15). None for non-app callers.
    app_id: str | None = None
    # The calling INSTALL's id, when a SOLUTION WORKFLOW is executing (the SDK
    # appends ?solution=<id> from the ExecutionContext). The workflow analog of
    # app_id: lets a workflow's `sdk.tables.get("name")` resolve its OWN install's
    # table first. None outside a solution execution.
    solution_id: str | None = None
    # SPIKE: the caller's OWN install on per-call targeted requests. The SDK
    # sends ?caller_solution=<inherited_id> alongside an explicit ?solution=
    # target so the server can tell own-calls (caller == target, always allow)
    # from cross-install calls (target's allow_inbound_access decides). UUID
    # format only; malformed values are ignored (caller treated as outside).
    # Trusted on engine requests only — resolvers decide via is_engine_user.
    caller_solution_id: str | None = None
    # A server-validated local Solution preview context.  This is never read
    # from a client-supplied app header and is set only after its signed token,
    # human subject, canonical Platform Admin grant, and install membership are
    # revalidated for this request.
    dev_solution_id: str | None = None

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
    """Get the current user using normal browser-wide credential precedence."""
    return await _get_current_user_optional(request, credentials, db)


async def _get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    db: DbSession,
    *,
    allow_impersonation_cookie: bool = True,
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
        db: Database session (used to verify engine-token execution liveness)

    Returns:
        UserPrincipal if authenticated, None otherwise
    """
    token = None
    token_type = "access"

    token = select_http_auth_token(
        request,
        credentials.credentials if credentials else None,
        allow_impersonation_cookie=allow_impersonation_cookie,
    )

    if not token:
        return None

    # Decode and validate token
    payload = decode_token(token, expected_type=token_type)

    if payload is None:
        return None

    if getattr(request.state, "runtime_token", None) == token:
        return request.state.runtime_principal
    runtime_principal = await load_runtime_principal(db, payload)
    if runtime_principal is not None:
        return runtime_principal
    # A legacy engine/service claim, malformed typed credential, or runtime
    # record that is no longer valid never falls through to human-token auth.
    if is_runtime_credential_payload(payload):
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

    try:
        impersonated_by_user_id = (
            UUID(payload["impersonated_by_user_id"])
            if payload.get("impersonated_by_user_id") is not None
            else None
        )
        impersonation_session_id = (
            UUID(payload["impersonation_session_id"])
            if payload.get("impersonation_session_id") is not None
            else None
        )
    except (TypeError, ValueError):
        return None
    if (impersonated_by_user_id is None) != (impersonation_session_id is None):
        return None

    principal = UserPrincipal(
        user_id=user_id,
        email=payload.get("email", ""),
        organization_id=organization_id,
        name=payload.get("name", ""),
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        is_external=payload.get("is_external", False),
        is_provider_org=payload.get("is_provider_org", False),
        roles=payload.get("roles", []),
        embed=payload.get("embed", False),
        embed_kind=payload.get("embed_kind"),
        grant=payload.get("grant"),
        jti=payload.get("jti"),
        app_id=payload.get("app_id"),
        form_id=payload.get("form_id"),
        verified_params=payload.get("verified_params"),
        verified_context=payload.get("verified_context"),
        capability_fingerprint=payload.get("capability_fingerprint"),
        token_exp=payload.get("exp"),
        impersonated_by_user_id=impersonated_by_user_id,
        impersonation_session_id=impersonation_session_id,
    )
    if impersonation_session_id is not None:
        try:
            await validate_browser_impersonation(db, principal)
        except BrowserImpersonationError:
            return None
    return principal


async def get_original_human_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> UserPrincipal:
    """Authenticate the original browser operator for impersonation Exit.

    The normal dependency must never be used here: an impersonation marker
    intentionally overrides ordinary authentication everywhere else. A still
    live refresh JTI lets the operator clear an expired marker after their
    original access JWT has naturally expired.
    """
    user = await _get_current_user_optional(
        request,
        credentials,
        db,
        allow_impersonation_cookie=False,
    )
    if user is not None and not (
        user.embed or user.runtime_authority is not None or user.impersonation_session_id is not None
    ):
        return user

    refresh_token = request.cookies.get("refresh_token")
    if refresh_token is not None:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = payload.get("sub") if payload else None
        jti = payload.get("jti") if payload else None
        if isinstance(user_id, str) and isinstance(jti, str):
            try:
                operator_id = UUID(user_id)
            except ValueError:
                operator_id = None
            if operator_id is not None:
                from src.core.cache import get_shared_redis
                from src.core.cache.keys import refresh_token_jti_key
                from src.models.orm.users import User

                redis = await get_shared_redis()
                if await redis.exists(refresh_token_jti_key(str(operator_id), jti)):
                    operator = await db.get(User, operator_id)
                    if operator is not None and operator.is_active:
                        return UserPrincipal(
                            user_id=operator.id,
                            email=operator.email,
                            organization_id=operator.organization_id,
                            name=operator.name or "",
                            is_active=operator.is_active,
                            is_superuser=operator.is_superuser,
                            is_verified=operator.is_verified,
                            is_external=operator.is_external,
                        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Original operator authentication is required",
        headers={"WWW-Authenticate": "Bearer"},
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


async def get_execution_context(
    request: Request,
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

    dev_context = None
    raw_dev_context = request.headers.get("X-Bifrost-Dev-Context")
    if raw_dev_context:
        from shared.solution_dev import validate_solution_dev_context

        dev_context = await validate_solution_dev_context(db, user, raw_dev_context)

    # Appended by the SDK (?solution=<install_id>) when a solution workflow is
    # executing.  Before forwarding it to the context, load the Solution row and
    # refuse execution for any install that has been uninstalled (status !=
    # "active").  Browse/export endpoints resolve solution_id via URL path params
    # and do NOT pass through this code-path, so they remain accessible for
    # inactive installs.
    solution_id_param = request.query_params.get("solution")
    if solution_id_param is not None:
        from src.models.orm.solutions import Solution as SolutionORM
        try:
            solution_uuid = UUID(solution_id_param)
        except ValueError:
            # SPIKE: slug/name ref — leave for router-level resolution inside
            # the resolved org scope (see resolve_solution_ref). Auth only
            # gates UUIDs here; slugs 404 later if unresolvable.
            solution_uuid = None
        if solution_uuid is not None:
            solution_row = await db.get(SolutionORM, solution_uuid)
            if solution_row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Solution not found",
                )
            await _refuse_if_solution_inactive(solution_row)

    # Gate: v2 SDK apps send X-Bifrost-App: <app_id>.  If that app belongs to a
    # solution, the solution must be active — otherwise the app is down along
    # with its install.  Fail closed only when the app DOES resolve to a known
    # inactive solution; a missing/unknown app_id leaves existing behaviour
    # unchanged (no 500 on a bad header value).
    requested_app_id = request.headers.get("X-Bifrost-App")
    if dev_context is not None and requested_app_id not in (None, str(dev_context.app_id)):
        from shared.solution_dev import INVALID_DEV_CONTEXT_DETAIL

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=INVALID_DEV_CONTEXT_DETAIL)
    # For local Python workflows there is no browser app header.  The validated
    # context still supplies the app/install identity to the normal resolution
    # path, without treating the capability token as bearer authentication.
    app_id_header = str(dev_context.app_id) if dev_context is not None else requested_app_id
    app_solution_id: UUID | None = None
    if app_id_header is not None:
        from src.models.orm.applications import Application as ApplicationORM
        from src.models.orm.solutions import Solution as SolutionORM
        try:
            app_uuid = UUID(app_id_header)
        except ValueError:
            app_uuid = None
        if app_uuid is not None:
            app_row = await db.get(ApplicationORM, app_uuid)
            if app_row is not None and app_row.solution_id is not None:
                app_solution_id = app_row.solution_id
                sol_row = await db.get(SolutionORM, app_row.solution_id)
                if sol_row is not None:
                    await _refuse_if_solution_inactive(sol_row)
    if solution_id_param is not None and app_solution_id is not None and dev_context is None:
        try:
            if UUID(solution_id_param) != app_solution_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="X-Bifrost-App does not belong to the requested solution",
                )
        except ValueError:
            # SPIKE: slug ref — router resolves it; mismatch surfaces as 404 there.
            pass

    effective_solution_id = solution_id_param or (
        str(dev_context.solution_id)
        if dev_context is not None
        else str(app_solution_id) if app_solution_id is not None else None
    )

    # SPIKE: per-call caller attestation (see field docs). Format-gated only;
    # trust is decided per-request by resolvers (engine requests only).
    caller_solution_id: str | None = None
    raw_caller = request.query_params.get("caller_solution")
    if raw_caller:
        try:
            UUID(raw_caller)
            caller_solution_id = raw_caller
        except ValueError:
            caller_solution_id = None

    return ExecutionContext(
        user=user,
        org_id=user.organization_id,
        db=db,
        # Set by the v2 SDK provider for Solution apps; harmless/None otherwise.
        app_id=app_id_header,
        solution_id=effective_solution_id,
        caller_solution_id=caller_solution_id,
        dev_solution_id=str(dev_context.solution_id) if dev_context is not None else None,
    )


async def _refuse_if_solution_inactive(solution_row: object) -> None:
    """Raise 409 if the given Solution ORM row is not active.

    Shared by the ?solution= and X-Bifrost-App gate branches so the error
    message stays consistent.
    """
    if getattr(solution_row, "status", None) != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Solution is inactive (uninstalled) and cannot serve execution requests",
        )


# Type aliases for dependency injection
CurrentUser = Annotated[UserPrincipal, Depends(get_current_user)]
CurrentActiveUser = Annotated[UserPrincipal, Depends(get_current_active_user)]
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

    Checks explicit credentials before the ordinary access-token cookie:
    Authorization header, then query token, then cookie. The query form is
    needed for browser WebSockets, whose API cannot set Authorization headers.

    Args:
        websocket: FastAPI WebSocket connection

    Returns:
        UserPrincipal if authenticated, None otherwise
    """
    explicit_token = None

    # Try Authorization header (some WebSocket clients support this)
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        explicit_token = auth_header[7:]

    if not explicit_token:
        explicit_token = websocket.query_params.get("token")

    # Preserve explicit/query then ordinary-cookie precedence, then apply the
    # same marker override as HTTP. Runtime and embed credentials remain
    # independent from browser impersonation.
    token = explicit_token or websocket.cookies.get("access_token") or websocket.cookies.get("embed_token")
    marker = websocket.cookies.get(IMPERSONATION_COOKIE_NAME)
    if marker:
        selected_payload = decode_token(token, expected_type="access") if token else None
        if not (
            selected_payload
            and (is_runtime_credential_payload(selected_payload) or selected_payload.get("embed"))
        ):
            token = marker

    if not token:
        return None

    # Decode and validate token - must be an access token
    payload = decode_token(token, expected_type="access")
    if payload is None:
        return None

    # WebSockets have no database dependency to resolve typed runtime
    # credentials. They must not accept them through the ordinary token path.
    if is_runtime_credential_payload(payload):
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

    try:
        impersonated_by_user_id = (
            UUID(payload["impersonated_by_user_id"])
            if payload.get("impersonated_by_user_id") is not None
            else None
        )
        impersonation_session_id = (
            UUID(payload["impersonation_session_id"])
            if payload.get("impersonation_session_id") is not None
            else None
        )
    except (TypeError, ValueError):
        return None
    if (impersonated_by_user_id is None) != (impersonation_session_id is None):
        return None

    principal = UserPrincipal(
        user_id=user_id,
        email=payload.get("email", ""),
        organization_id=organization_id,
        name=payload.get("name", ""),
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        is_external=payload.get("is_external", False),
        is_provider_org=payload.get("is_provider_org", False),
        roles=payload.get("roles", []),
        embed=payload.get("embed", False),
        embed_kind=payload.get("embed_kind"),
        grant=payload.get("grant"),
        jti=payload.get("jti"),
        app_id=payload.get("app_id"),
        form_id=payload.get("form_id"),
        verified_params=payload.get("verified_params"),
        verified_context=payload.get("verified_context"),
        capability_fingerprint=payload.get("capability_fingerprint"),
        token_exp=payload.get("exp"),
        impersonated_by_user_id=impersonated_by_user_id,
        impersonation_session_id=impersonation_session_id,
    )
    if impersonation_session_id is not None:
        from src.core.database import get_db_context

        try:
            async with get_db_context() as db:
                await validate_browser_impersonation(db, principal)
        except BrowserImpersonationError:
            return None
    return principal
