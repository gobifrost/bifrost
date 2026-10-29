"""
Bifrost API - FastAPI Application

Main entry point for the FastAPI application.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from src.config import get_settings
from src.core.cache.redis_client import close_shared_redis, initialize_shared_redis
from src.core.csrf import CSRFMiddleware
from src.core.embed_middleware import EmbedScopeMiddleware
from src.core.request_body_limit import RouteBodyLimitMiddleware
from src.core.database import close_db, get_session_factory, init_db
from src.core.pubsub import manager as pubsub_manager
from src.routers.agent_runs import router as agent_runs_router
from src.routers.agent_tuning import router as agent_tuning_router
from src.routers.agents import router as agents_router
from src.routers.ai_models import router as ai_models_router
from src.routers.ai_pricing import router as ai_pricing_router
from src.routers.app_code_files import router as app_code_files_router
from src.routers.app_code_files import render_router as app_render_router
from src.routers.app_embed_secrets import router as app_embed_secrets_router
from src.routers.applications import router as applications_router
from src.routers.audit import router as audit_router
from src.routers.auth import router as auth_router
from src.routers.authorization import router as authorization_router
from src.routers.branding import router as branding_router
from src.routers.chat import router as chat_router
from src.routers.claims import router as claims_router
from src.routers.cli import install_router as cli_install_router
from src.routers.cli import router as cli_router
from src.routers.config import router as config_router
from src.routers.decorator_properties import router as decorator_properties_router
from src.routers.dependencies import router as dependencies_router
from src.routers.docs import router as docs_router
from src.routers.embed import router as embed_router
from src.routers.endpoints import router as endpoints_router
from src.routers.events import router as events_router
from src.routers.executions import router as executions_router
from src.routers.export_import import router as export_import_router
from src.routers.files import router as files_router
from src.routers.form_embed_secrets import router as form_embed_secrets_router
from src.routers.forms import router as forms_router
from src.routers.github import router as github_router
from src.routers.health import close_health_check_clients
from src.routers.health import router as health_router
from src.routers.hooks import router as hooks_router
from src.routers.identities import router as identities_router
from src.routers.integrations import router as integrations_router
from src.routers.jobs import router as jobs_router
from src.routers.knowledge_sources import router as knowledge_sources_router
from src.routers.kubernetes import admin_router as kubernetes_admin_router
from src.routers.llm_config import router as llm_config_router
from src.routers.maintenance import router as maintenance_router
from src.routers.mcp import router as mcp_router
from src.routers.mcp_connections import me_router as mcp_me_connections_router
from src.routers.mcp_connections import router as mcp_connections_router
from src.routers.mcp_oauth_callback import router as mcp_oauth_callback_router
from src.routers.mcp_servers import router as mcp_servers_router
from src.routers.memory import admin_router as memory_admin_router
from src.routers.memory import router as memory_router
from src.routers.metrics import router as metrics_router
from src.routers.mfa import router as mfa_router
from src.routers.model_catalog import router as model_catalog_router
from src.routers.notifications import router as notifications_router
from src.routers.oauth_config import router as oauth_config_router
from src.routers.oauth_connections import router as oauth_connections_router
from src.routers.oauth_sso import router as oauth_router
from src.routers.organizations import router as organizations_router
from src.routers.packages import router as packages_router
from src.routers.passkeys import router as passkeys_router
from src.routers.permissions import router as permissions_router
from src.routers.platform import queue_router as platform_queue_router
from src.routers.platform import stuck_router as platform_stuck_router
from src.routers.platform import workers_router as platform_workers_router
from src.routers.platform_jobs import router as platform_jobs_router
from src.routers.policy_rules import router as policy_rules_router
from src.routers.product_updates import router as product_updates_router
from src.routers.profile import router as profile_router
from src.routers.required_instructions import (
    admin_router as required_instructions_admin_router,
)
from src.routers.required_instructions import router as required_instructions_router
from src.routers.roi_reports import router as roi_reports_router
from src.routers.roi_settings import router as roi_settings_router
from src.routers.roles import router as roles_router
from src.routers.run_retention import router as run_retention_router
from src.routers.scheduler_diagnostics import router as scheduler_diagnostics_router
from src.routers.schedules import router as schedules_router
from src.routers.sdk_modules import router as sdk_modules_router
from src.routers.sdk_runtime import router as sdk_runtime_router
from src.routers.services import router as services_router
from src.routers.solutions import router as solutions_router
from src.routers.tables import router as tables_router
from src.routers.tools import router as tools_router
from src.routers.usage_reports import router as usage_reports_router
from src.routers.users import router as users_router
from src.routers.version import router as version_router
from src.routers.websocket import router as websocket_router
from src.routers.workflow_keys import router as workflow_keys_router
from src.routers.workflows import router as workflows_router
from src.worker.remote.host import (
    router as remote_control_router,
    start_remote_control_host,
    stop_remote_control_host,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Suppress noisy third-party loggers
logging.getLogger("apscheduler").setLevel(logging.WARNING)
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

    await initialize_shared_redis()
    logger.info("Redis cache connection established")

    # Register dynamic workflow endpoints for OpenAPI documentation
    logger.info("Registering workflow endpoints...")
    await register_dynamic_workflow_endpoints(app)

    # Create default admin user if configured via environment variables
    if settings.default_user_email and settings.default_user_password:
        await create_default_user()

    # Seed built-in policy rules (idempotent; must exist before any file prefix
    # with {"$ref": "admin_bypass"} is created).
    try:
        from src.services.policy_rule_service import PolicyRuleService

        session_factory = get_session_factory()
        async with session_factory() as db:
            await PolicyRuleService(db).seed_builtin_admin_bypass()
            await db.commit()
            logger.info("Built-in policy rules seeded")
    except Exception as e:
        logger.warning(f"Built-in policy rule seeding failed: {e}")

    # Settings -> Kubernetes takes its deployment variables before this
    # replica serves: platform control (which also imports) joins only in
    # the background, and a build enqueued before then would read the
    # defaults. Idempotent; never fails startup.
    try:
        from src.services.kubernetes_execution import import_kubernetes_policy_from_env

        async with get_session_factory()() as db:
            await import_kubernetes_policy_from_env(db, os.environ)
    except Exception as e:
        logger.warning(f"Kubernetes settings import failed: {e}")

    # Name deployment variables this release no longer reads, or reads under
    # a new name (API only: worker Jobs copy this pod's environment). Never
    # fails startup.
    try:
        from src.services.deployment_environment import warn_about_environment
        from src.services.legacy_broker_drain import legacy_broker_drained

        async with get_session_factory()() as db:
            broker_drained = await legacy_broker_drained(db)
        warn_about_environment(os.environ, broker_drained=broker_drained)
    except Exception as e:
        logger.warning(f"Environment variable check failed: {e}")

    from src.scheduler.api_host import ApiControlHost

    control_host = ApiControlHost()
    await control_host.start()
    logger.info("Platform control hosted by this API replica")

    await start_remote_control_host(app)
    logger.info("Remote execution control hosted by this API replica")

    from src.services.legacy_broker_drain import drain_legacy_broker

    legacy_drain = asyncio.create_task(drain_legacy_broker(), name="legacy-broker-drain")

    logger.info(f"Bifrost API started in {settings.environment} mode")

    yield

    # Shutdown
    logger.info("Shutting down Bifrost API...")

    # Stop accepting launchers and drain the API-owned consumers while the
    # database and transport clients are still available.
    await stop_remote_control_host()

    # An unfinished drain leaves its unacknowledged message queued.
    legacy_drain.cancel()
    await asyncio.gather(legacy_drain, return_exceptions=True)

    # Surrender the trigger lease while the database is still open.
    await control_host.stop()

    from src.services.agent_runtime.retry_transport import (
        close_ai_retry_http_client,
    )

    await close_ai_retry_http_client()
    await pubsub_manager.close()
    await close_shared_redis()
    await close_health_check_clients()
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
            if settings.debug:
                # In debug stacks the configured seed credential is the source
                # of truth. This lets debug.sh replace the shared development
                # password before a NetBird public proxy is enabled, including
                # for a stack created before public exposure was introduced.
                existing.hashed_password = get_password_hash(
                    settings.default_user_password
                )
                existing.mfa_enabled = False
                await db.commit()
                logger.info(
                    "Synchronized debug user credentials: %s",
                    settings.default_user_email,
                )
                return
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
    # Shared with the worker-local engine SDK app so SDK calls over the
    # worker's Unix socket map errors exactly like HTTP.
    from src.core.app_wiring import register_exception_handlers

    register_exception_handlers(app)

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

    # This route accepts multipart uploads.  The cap must run before
    # Starlette constructs UploadFile and spills a body to disk.
    from src.services.solutions.zip_install import (
        MAX_SOLUTION_ARCHIVE_BYTES,
        MAX_SOLUTION_BACKUP_ARCHIVE_BYTES,
    )
    app.add_middleware(
        RouteBodyLimitMiddleware,
        limits={
            ("POST", "/api/solutions/import-workspace/preview"): MAX_SOLUTION_ARCHIVE_BYTES,
            ("POST", "/api/solutions/install/preview"): MAX_SOLUTION_BACKUP_ARCHIVE_BYTES,
            ("POST", "/api/solutions/install"): MAX_SOLUTION_BACKUP_ARCHIVE_BYTES,
        },
    )

    # Add CSRF protection middleware
    # Only enforces for cookie-based auth with unsafe methods (POST, PUT, DELETE, PATCH)
    # Bearer token auth is exempt since browsers don't automatically include it
    app.add_middleware(CSRFMiddleware)

    # Restrict embed tokens to app-rendering endpoints only
    app.add_middleware(EmbedScopeMiddleware)

    # Set request-scoped ContextVars for user attribution, session tracking,
    # and the audit actor. Shared with the worker-local engine SDK app.
    from src.core.app_wiring import install_request_context_middleware

    install_request_context_middleware(app)

    # Register routers
    from src.routers.home import router as home_router
    app.include_router(home_router)
    app.include_router(health_router)
    app.include_router(version_router)
    app.include_router(auth_router)
    app.include_router(authorization_router)
    app.include_router(mfa_router)
    app.include_router(oauth_router)
    app.include_router(passkeys_router)
    app.include_router(organizations_router)
    app.include_router(users_router)
    app.include_router(roles_router)
    app.include_router(permissions_router)
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
    app.include_router(platform_jobs_router)
    app.include_router(scheduler_diagnostics_router)
    app.include_router(oauth_connections_router)
    app.include_router(endpoints_router)
    app.include_router(cli_router)
    app.include_router(cli_install_router)
    app.include_router(notifications_router)
    app.include_router(profile_router)
    app.include_router(memory_router)
    app.include_router(memory_admin_router)
    app.include_router(kubernetes_admin_router)
    app.include_router(required_instructions_router)
    app.include_router(required_instructions_admin_router)
    app.include_router(agents_router)
    app.include_router(agent_runs_router)
    app.include_router(agent_tuning_router)
    app.include_router(chat_router)
    app.include_router(llm_config_router)
    app.include_router(ai_models_router)
    app.include_router(model_catalog_router)
    app.include_router(integrations_router)
    app.include_router(decorator_properties_router)
    app.include_router(maintenance_router)
    app.include_router(run_retention_router)
    app.include_router(identities_router)
    app.include_router(roi_settings_router)
    app.include_router(roi_reports_router)
    app.include_router(usage_reports_router)
    app.include_router(ai_pricing_router)
    app.include_router(oauth_config_router)
    app.include_router(tools_router)
    app.include_router(mcp_router)
    app.include_router(events_router)
    app.include_router(services_router)
    app.include_router(hooks_router)
    app.include_router(tables_router)
    app.include_router(claims_router)
    app.include_router(solutions_router)
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
    app.include_router(sdk_modules_router)
    app.include_router(sdk_runtime_router)
    app.include_router(policy_rules_router)
    app.include_router(product_updates_router)
    # The FastMCP application below mounts at the root and catches unmatched
    # paths, so this WebSocket route must precede it.
    app.include_router(remote_control_router)

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
