"""Data access layer repository exports.

Repository modules are resolved lazily so importing one repository submodule does
not import every repository and its transitive model/service graph.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_REPOSITORY_EXPORTS = {
    "AccessDeniedError": ("src.core.exceptions", "AccessDeniedError"),
    "BaseRepository": ("src.repositories.base", "BaseRepository"),
    "CLISessionRepository": ("src.repositories.cli_sessions", "CLISessionRepository"),
    "ConfigRepository": ("src.repositories.config", "ConfigRepository"),
    "DataProviderRepository": ("src.repositories.data_providers", "DataProviderRepository"),
    "ExecutionLogRepository": ("src.repositories.execution_logs", "ExecutionLogRepository"),
    "ExecutionRepository": ("src.repositories.executions", "ExecutionRepository"),
    "IntegrationMappingRepository": (
        "src.repositories.integrations",
        "IntegrationMappingRepository",
    ),
    "KnowledgeDocument": ("src.repositories.knowledge", "KnowledgeDocument"),
    "KnowledgeRepository": ("src.repositories.knowledge", "KnowledgeRepository"),
    "NamespaceInfo": ("src.repositories.knowledge", "NamespaceInfo"),
    "OAuthProviderRepository": ("src.repositories.oauth", "OAuthProviderRepository"),
    "OAuthTokenRepository": ("src.repositories.oauth", "OAuthTokenRepository"),
    "OrgScopedRepository": ("src.repositories.org_scoped", "OrgScopedRepository"),
    "OrganizationRepository": ("src.repositories.organizations", "OrganizationRepository"),
    "TableRepository": ("src.repositories.tables", "TableRepository"),
    "UserRepository": ("src.repositories.users", "UserRepository"),
    "WorkflowRepository": ("src.repositories.workflows", "WorkflowRepository"),
    "create_execution": ("src.repositories.executions", "create_execution"),
    "update_execution": ("src.repositories.executions", "update_execution"),
}

if TYPE_CHECKING:
    from src.core.exceptions import AccessDeniedError as AccessDeniedError
    from src.repositories.base import BaseRepository as BaseRepository
    from src.repositories.cli_sessions import CLISessionRepository as CLISessionRepository
    from src.repositories.config import ConfigRepository as ConfigRepository
    from src.repositories.data_providers import DataProviderRepository as DataProviderRepository
    from src.repositories.execution_logs import ExecutionLogRepository as ExecutionLogRepository
    from src.repositories.executions import (
        ExecutionRepository as ExecutionRepository,
        create_execution as create_execution,
        update_execution as update_execution,
    )
    from src.repositories.integrations import (
        IntegrationMappingRepository as IntegrationMappingRepository,
    )
    from src.repositories.knowledge import (
        KnowledgeDocument as KnowledgeDocument,
        KnowledgeRepository as KnowledgeRepository,
        NamespaceInfo as NamespaceInfo,
    )
    from src.repositories.oauth import (
        OAuthProviderRepository as OAuthProviderRepository,
        OAuthTokenRepository as OAuthTokenRepository,
    )
    from src.repositories.org_scoped import OrgScopedRepository as OrgScopedRepository
    from src.repositories.organizations import OrganizationRepository as OrganizationRepository
    from src.repositories.tables import TableRepository as TableRepository
    from src.repositories.users import UserRepository as UserRepository
    from src.repositories.workflows import WorkflowRepository as WorkflowRepository

__all__ = [
    "AccessDeniedError",
    "BaseRepository",
    "CLISessionRepository",
    "ConfigRepository",
    "DataProviderRepository",
    "ExecutionLogRepository",
    "ExecutionRepository",
    "IntegrationMappingRepository",
    "KnowledgeDocument",
    "KnowledgeRepository",
    "NamespaceInfo",
    "OAuthProviderRepository",
    "OAuthTokenRepository",
    "OrgScopedRepository",
    "OrganizationRepository",
    "TableRepository",
    "UserRepository",
    "WorkflowRepository",
    # Standalone functions for workers/consumers
    "create_execution",
    "update_execution",
]


def __getattr__(name: str) -> Any:
    try:
        module_name, export_name = _REPOSITORY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'src.repositories' has no attribute {name!r}") from exc

    value = getattr(import_module(module_name), export_name)
    globals()[name] = value
    return value
