# FastAPI Routers
from src.routers.auth import router as auth_router
from src.routers.mfa import router as mfa_router
from src.routers.oauth_sso import router as oauth_router
from src.routers.passkeys import router as passkeys_router
from src.routers.health import router as health_router
from src.routers.organizations import router as organizations_router
from src.routers.users import router as users_router
from src.routers.roles import router as roles_router
from src.routers.executions import router as executions_router
from src.routers.workflows import router as workflows_router
from src.routers.forms import router as forms_router
from src.routers.config import router as config_router
from src.routers.websocket import router as websocket_router
from src.routers.branding import router as branding_router
from src.routers.files import router as files_router
from src.routers.schedules import router as schedules_router
from src.routers.workflow_keys import router as workflow_keys_router
from src.routers.audit import router as audit_router
from src.routers.metrics import router as metrics_router
from src.routers.packages import router as packages_router
from src.routers.github import router as github_router
from src.routers.oauth_connections import router as oauth_connections_router
from src.routers.endpoints import router as endpoints_router
from src.routers.cli import router as cli_router
from src.routers.notifications import router as notifications_router
from src.routers.profile import router as profile_router
from src.routers.agents import router as agents_router
from src.routers.agent_runs import router as agent_runs_router
from src.routers.agent_tuning import router as agent_tuning_router
from src.routers.chat import router as chat_router
from src.routers.llm_config import router as llm_config_router
from src.routers.integrations import router as integrations_router
from src.routers.decorator_properties import router as decorator_properties_router
from src.routers.maintenance import router as maintenance_router
from src.routers.roi_settings import router as roi_settings_router
from src.routers.roi_reports import router as roi_reports_router
from src.routers.usage_reports import router as usage_reports_router
from src.routers.ai_pricing import router as ai_pricing_router
from src.routers.oauth_config import router as oauth_config_router
from src.routers.tools import router as tools_router
from src.routers.mcp import router as mcp_router
from src.routers.events import router as events_router
from src.routers.hooks import router as hooks_router
from src.routers.tables import router as tables_router
from src.routers.knowledge_sources import router as knowledge_sources_router
from src.routers.app_embed_secrets import router as app_embed_secrets_router
from src.routers.applications import router as applications_router
from src.routers.app_code_files import router as app_code_files_router
from src.routers.app_code_files import render_router as app_render_router
from src.routers.dependencies import router as dependencies_router
from src.routers.embed import router as embed_router
from src.routers.form_embed_secrets import router as form_embed_secrets_router
from src.routers.export_import import router as export_import_router
from src.routers.docs import router as docs_router
from src.routers.jobs import router as jobs_router
from src.routers.platform import (
    workers_router as platform_workers_router,
    queue_router as platform_queue_router,
    stuck_router as platform_stuck_router,
)
from src.routers.version import router as version_router
from src.routers.mcp_servers import router as mcp_servers_router
from src.routers.mcp_connections import (
    router as mcp_connections_router,
    me_router as mcp_me_connections_router,
)
from src.routers.mcp_oauth_callback import router as mcp_oauth_callback_router

__all__ = [
    "auth_router",
    "mfa_router",
    "oauth_router",
    "passkeys_router",
    "health_router",
    "organizations_router",
    "users_router",
    "roles_router",
    "executions_router",
    "workflows_router",
    "forms_router",
    "config_router",
    "websocket_router",
    "branding_router",
    "files_router",
    "schedules_router",
    "workflow_keys_router",
    "audit_router",
    "metrics_router",
    "packages_router",
    "github_router",
    "oauth_connections_router",
    "endpoints_router",
    "cli_router",
    "notifications_router",
    "profile_router",
    "agents_router",
    "agent_runs_router",
    "agent_tuning_router",
    "chat_router",
    "llm_config_router",
    "integrations_router",
    "decorator_properties_router",
    "maintenance_router",
    "roi_settings_router",
    "roi_reports_router",
    "usage_reports_router",
    "ai_pricing_router",
    "oauth_config_router",
    "tools_router",
    "mcp_router",
    "events_router",
    "hooks_router",
    "tables_router",
    "knowledge_sources_router",
    "app_embed_secrets_router",
    "applications_router",
    "app_code_files_router",
    "app_render_router",
    "dependencies_router",
    "embed_router",
    "form_embed_secrets_router",
    "export_import_router",
    "docs_router",
    "jobs_router",
    "platform_workers_router",
    "platform_queue_router",
    "platform_stuck_router",
    "version_router",
    "mcp_servers_router",
    "mcp_connections_router",
    "mcp_me_connections_router",
    "mcp_oauth_callback_router",
]
