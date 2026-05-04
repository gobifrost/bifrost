"""
Bifrost API - FastAPI Application

Main entry point for the FastAPI application.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError, NoResultFound, OperationalError

from src.config import get_settings
from src.models.contracts.common import ErrorResponse
from src.core.csrf import CSRFMiddleware
from src.core.embed_middleware import EmbedScopeMiddleware
from src.core.database import close_db, init_db
from src.core.pubsub import manager as pubsub_manager
from src.routers import (
    auth_router,
    mfa_router,
    oauth_router,
    passkeys_router,
    health_router,
    organizations_router,
    users_router,
    roles_router,
    executions_router,
    workflows_router,
    forms_router,
    config_router,
    websocket_router,
    branding_router,
    files_router,
    schedules_router,
    workflow_keys_router,
    audit_router,
    metrics_router,
    packages_router,
    github_router,
    jobs_router,
    oauth_connections_router,
    endpoints_router,
    cli_router,
    notifications_router,
    profile_router,
    agent_runs_router,
    agent_tuning_router,
    agents_router,
    chat_router,
    llm_config_router,
    integrations_router,
    decorator_properties_router,
    maintenance_router,
    roi_settings_router,
    roi_reports_router,
    usage_reports_router,
    ai_pricing_router,
    email_config_router,
    email_sdk_router,
    oauth_config_router,
    tools_router,
    mcp_router,
    events_router,
    hooks_router,
    tables_router,
    knowledge_sources_router,
    app_embed_secrets_router,
    applications_router,
    app_code_files_router,
    app_render_router,
    dependencies_router,
    embed_router,
    form_embed_secrets_router,
    export_import_router,
    docs_router,
    platform_workers_router,
    platform_queue_router,
    platform_stuck_router,
    version_router,
    mcp_servers_router,
    mcp_connections_router,
    mcp_me_connections_router,
    mcp_oauth_callback_router,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Suppress noisy third-party loggers
logging.getLogger("aiormq").setLevel(logging.WARNING)
logging.getLogger("aio_pika").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("aiobotocore").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Enable DEBUG for execution engine to troubleshoot workflows
logging.getLogger("src.services.execution").setLevel(logging.DEBUG)
logging.getLogger("bifrost").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Core application lifespan manager.

    Handles startup and shutdown events for the main application.
    """
    # Startup
    logger.info("Starting Bifrost API...")
    settings = get_settings()

    # Initialize database
    logger.info("Initializing database connection...")
    await init_db()
    logger.info("Database connection established")

    # Register entity change hooks for real-time manifest sync
    from src.core.entity_change_hook import register_entity_change_hooks
    register_entity_change_hooks()

    # Register dynamic workflow endpoints for OpenAPI documentation
    logger.info("Registering workflow endpoints...")
    await register_dynamic_workflow_endpoints(app)

    # Create default admin user if configured via environment variables
    if settings.default_user_email and settings.default_user_password:
        await create_default_user()

    # Reconcile file_index with S3 _repo/ in background
    from src.services.file_index_reconciler import reconcile_file_index
    from src.core.database import get_session_factory

    async def _run_reconciler():
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                stats = await reconcile_file_index(db)
                await db.commit()
                logger.info(f"File index reconciliation complete: {stats}")
        except Exception as e:
            logger.warning(f"File index reconciliation failed: {e}")

    asyncio.create_task(_run_reconciler())

    logger.info(f"Bifrost API started in {settings.environment} mode")

    yield

    # Shutdown
    logger.info("Shutting down Bifrost API...")

    await pubsub_manager.close()
    await close_db()
    logger.info("Bifrost API shutdown complete")


# MCP ASGI app cached at module level for lifespan access
_mcp_asgi_app = None


def _get_mcp_asgi_app():
    """Get or create the MCP ASGI app (cached)."""
    global _mcp_asgi_app
    if _mcp_asgi_app is None:
        try:
            from src.routers.mcp import get_mcp_asgi_app
            _mcp_asgi_app = get_mcp_asgi_app()
        except Exception as e:
            logger.warning(f"Could not create MCP ASGI app: {e}")
    return _mcp_asgi_app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Combined lifespan manager for FastAPI and FastMCP.

    Handles both the main app lifespan and MCP session manager initialization.
    """
    mcp_app = _get_mcp_asgi_app()

    # Combine both lifespans - app first, then MCP
    async with app_lifespan(app):
        if mcp_app and hasattr(mcp_app, 'lifespan'):
            async with mcp_app.lifespan(app):
                yield
        else:
            yield


async def register_dynamic_workflow_endpoints(app: FastAPI) -> None:
    """
    Register dynamic routes for endpoint-enabled workflows.

    This creates per-workflow OpenAPI documentation with proper parameter
    schemas for each workflow that has endpoint_enabled=True.
    """
    from src.core.database import get_db_context
    from src.services.openapi_endpoints import register_workflow_endpoints

    try:
        async with get_db_context() as db:
            count = await register_workflow_endpoints(app, db)
            logger.info(f"Registered {count} dynamic workflow endpoints")
    except Exception as e:
        # Don't fail startup if endpoint registration fails
        logger.warning(f"Failed to register workflow endpoints: {e}")


async def create_default_user() -> None:
    """
    Create default admin user if it doesn't exist.

    Only runs if BIFROST_DEFAULT_USER_EMAIL and BIFROST_DEFAULT_USER_PASSWORD
    environment variables are set. Routed through ensure_user_provisioned() so
    the user is fully scoped (organization_id = provider org), which makes
    has_users() return True and skips the setup wizard. mfa_enabled is forced
    off for password login on the seeded account.
    """
    from src.core.database import get_db_context
    from src.core.security import get_password_hash
    from src.repositories.users import UserRepository
    from src.services.user_provisioning import ensure_user_provisioned

    settings = get_settings()

    if not settings.default_user_email or not settings.default_user_password:
        return

    async with get_db_context() as db:
        user_repo = UserRepository(db)

        existing = await user_repo.get_by_email(settings.default_user_email)
        if existing:
            logger.info(f"Default user already exists: {settings.default_user_email}")
            return

        result = await ensure_user_provisioned(
            db=db,
            email=settings.default_user_email,
            name="Dev Admin",
        )
        user = result.user
        user.hashed_password = get_password_hash(settings.default_user_password)
        user.mfa_enabled = False
        await db.commit()
        logger.info(f"Created default admin user: {user.email} (id: {user.id})")



def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    from shared.version import get_version

    app = FastAPI(
        title="Bifrost API",
        description="MSP automation platform API",
        version=get_version(),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Note: CORS middleware not needed - frontend proxies all /api requests
    # through Vite dev server (dev) or nginx (prod), making them same-origin.

    # ==========================================================================
    # Global Exception Handlers
    # ==========================================================================
    # These provide consistent error responses using the ErrorResponse model.
    # Handlers are registered in order of specificity (most specific first).

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Request validation errors (bad input) → 422 with concise messages."""
        messages = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"] if p != "body")
            messages.append(f"{field}: {err['msg']}")
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="validation_error",
                message="; ".join(messages),
            ).model_dump(),
        )

    @app.exception_handler(PydanticValidationError)
    async def pydantic_validation_handler(
        request: Request, exc: PydanticValidationError
    ) -> JSONResponse:
        """Pydantic model validation errors → 422."""
        errors = exc.errors()
        # Extract field names and messages for user-friendly output
        field_errors = {
            ".".join(str(loc) for loc in e["loc"]): e["msg"] for e in errors
        }
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="validation_error",
                message="Validation failed",
                details={"fields": field_errors},
            ).model_dump(),
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(
        request: Request, exc: IntegrityError
    ) -> JSONResponse:
        """Database constraint violations → 409."""
        detail = str(exc.orig) if exc.orig else str(exc)

        if "unique" in detail.lower() or "duplicate" in detail.lower():
            message = "Resource already exists"
        elif "foreign key" in detail.lower():
            message = "Referenced resource not found"
        else:
            message = "Database constraint violation"

        logger.warning(f"IntegrityError: {detail}")
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(
                error="conflict",
                message=message,
            ).model_dump(),
        )

    @app.exception_handler(NoResultFound)
    async def no_result_handler(
        request: Request, exc: NoResultFound
    ) -> JSONResponse:
        """Query returned no results → 404."""
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error="not_found",
                message="Resource not found",
            ).model_dump(),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(
        request: Request, exc: ValueError
    ) -> JSONResponse:
        """ValueError from validation → 422."""
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="validation_error",
                message=str(exc),
            ).model_dump(),
        )

    @app.exception_handler(asyncio.TimeoutError)
    async def timeout_handler(
        request: Request, exc: asyncio.TimeoutError
    ) -> JSONResponse:
        """Timeout errors → 504."""
        logger.warning(f"Timeout error on {request.method} {request.url.path}")
        return JSONResponse(
            status_code=504,
            content=ErrorResponse(
                error="timeout",
                message="Operation timed out",
            ).model_dump(),
        )

    @app.exception_handler(OperationalError)
    async def operational_error_handler(
        request: Request, exc: OperationalError
    ) -> JSONResponse:
        """Database connection issues → 503."""
        logger.error(f"Database operational error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error="service_unavailable",
                message="Service temporarily unavailable",
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Catch-all for unhandled exceptions → 500 with safe message."""
        logger.error(
            f"Unhandled exception on {request.method} {request.url.path}: {exc}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="internal_error",
                message="An unexpected error occurred",
            ).model_dump(),
        )

    # ==========================================================================
    # Middleware
    # ==========================================================================

    # CORS for MCP OAuth discovery endpoints (/.well-known/*, /register, /authorize, /token)
    # These routes are on FastAPI directly, not the MCP ASGI app which has its own CORS.
    # Without this, browser-based MCP clients (e.g. MCP Inspector) can't read discovery responses.
    from starlette.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )

    # Add CSRF protection middleware
    # Only enforces for cookie-based auth with unsafe methods (POST, PUT, DELETE, PATCH)
    # Bearer token auth is exempt since browsers don't automatically include it
    app.add_middleware(CSRFMiddleware)

    # Restrict embed tokens to app-rendering endpoints only
    app.add_middleware(EmbedScopeMiddleware)

    # Set request-scoped ContextVars for user attribution and session tracking
    from src.core.request_context import RequestUser, set_request_user, set_request_session_id
    from src.core.rate_limit import get_client_ip
    from src.services.audit_context import ActorContext, set_actor, clear_actor

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        # Set watch session ID from header
        session_id = request.headers.get("x-bifrost-watch-session")
        set_request_session_id(session_id)

        # Parse token once; use for both request_user and audit actor contexts.
        audit_user_id = None
        audit_org_id = None
        audit_email = None
        audit_name = None
        try:
            from uuid import UUID as _UUID
            from src.core.security import decode_token
            token = None
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            elif "access_token" in request.cookies:
                token = request.cookies["access_token"]
            if token:
                payload = decode_token(token, expected_type="access")
                if payload:
                    user_id = payload.get("sub", "")
                    user_name = payload.get("name") or payload.get("email") or user_id
                    set_request_user(RequestUser(user_id=user_id, user_name=user_name))
                    # Populate audit actor fields
                    try:
                        audit_user_id = _UUID(user_id) if user_id else None
                    except ValueError:
                        audit_user_id = None
                    org_id_raw = payload.get("org_id")
                    if org_id_raw:
                        try:
                            audit_org_id = _UUID(org_id_raw)
                        except ValueError:
                            audit_org_id = None
                    audit_email = payload.get("email")
                    audit_name = payload.get("name")
            else:
                set_request_user(None)
        except Exception:
            set_request_user(None)

        # Always set audit actor context — for unauthenticated requests,
        # user_id is None but IP/UA are still captured (so failed logins
        # are recorded with network metadata).
        actor_token = set_actor(
            ActorContext(
                user_id=audit_user_id,
                organization_id=audit_org_id,
                email=audit_email,
                name=audit_name,
                ip_address=get_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                source="http",
            )
        )

        try:
            response = await call_next(request)
        finally:
            # Reset context after request
            set_request_user(None)
            set_request_session_id(None)
            clear_actor(actor_token)
        return response

    # Register routers
    app.include_router(health_router)
    app.include_router(version_router)
    app.include_router(auth_router)
    app.include_router(mfa_router)
    app.include_router(oauth_router)
    app.include_router(passkeys_router)
    app.include_router(organizations_router)
    app.include_router(users_router)
    app.include_router(roles_router)
    app.include_router(executions_router)
    app.include_router(workflows_router)
    app.include_router(forms_router)
    app.include_router(config_router)
    app.include_router(websocket_router)
    app.include_router(branding_router)
    app.include_router(files_router)
    app.include_router(schedules_router)
    app.include_router(workflow_keys_router)
    app.include_router(audit_router)
    app.include_router(metrics_router)
    app.include_router(packages_router)
    app.include_router(github_router)
    app.include_router(jobs_router)
    app.include_router(oauth_connections_router)
    app.include_router(endpoints_router)
    app.include_router(cli_router)
    app.include_router(notifications_router)
    app.include_router(profile_router)
    app.include_router(agents_router)
    app.include_router(agent_runs_router)
    app.include_router(agent_tuning_router)
    app.include_router(chat_router)
    app.include_router(llm_config_router)
    app.include_router(integrations_router)
    app.include_router(decorator_properties_router)
    app.include_router(maintenance_router)
    app.include_router(roi_settings_router)
    app.include_router(roi_reports_router)
    app.include_router(usage_reports_router)
    app.include_router(ai_pricing_router)
    app.include_router(email_config_router)
    app.include_router(email_sdk_router)
    app.include_router(oauth_config_router)
    app.include_router(tools_router)
    app.include_router(mcp_router)
    app.include_router(events_router)
    app.include_router(hooks_router)
    app.include_router(tables_router)
    app.include_router(knowledge_sources_router)
    app.include_router(app_embed_secrets_router)
    app.include_router(applications_router)
    app.include_router(app_code_files_router)
    app.include_router(app_render_router)
    app.include_router(dependencies_router)
    app.include_router(embed_router)
    app.include_router(form_embed_secrets_router)
    app.include_router(export_import_router)
    app.include_router(docs_router)
    app.include_router(platform_workers_router)
    app.include_router(platform_queue_router)
    app.include_router(platform_stuck_router)
    app.include_router(mcp_servers_router)
    app.include_router(mcp_connections_router)
    app.include_router(mcp_me_connections_router)
    app.include_router(mcp_oauth_callback_router)

    # Mount MCP OAuth routes at root level (required by RFC 8414/9728)
    # These must be registered BEFORE the FastMCP ASGI mount
    try:
        from src.services.mcp_server.auth import create_bifrost_auth_provider
        auth_provider = create_bifrost_auth_provider()
        for route in auth_provider.get_routes():
            app.add_api_route(
                route.path,
                route.endpoint,
                methods=list(route.methods) if route.methods else ["GET"],
                include_in_schema=False,  # Don't clutter OpenAPI docs
            )
        logger.info(f"Mounted {len(auth_provider.get_routes())} MCP OAuth routes")
    except Exception as e:
        logger.warning(f"Could not mount MCP OAuth routes: {e}")

    # Mount FastMCP ASGI app at root - FastMCP handles /mcp internally
    # This must be mounted AFTER all other routers since it catches unmatched routes
    mcp_asgi_app = _get_mcp_asgi_app()
    if mcp_asgi_app:
        app.mount("", mcp_asgi_app)
        logger.info("Mounted FastMCP ASGI app at root (MCP handles /mcp)")

    # Root endpoint
    @app.get("/")
    async def root():
        return {
            "name": "Bifrost API",
            "version": "2.0.0",
            "docs": "/docs",
        }

    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.is_development,
    )
