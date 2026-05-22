"""
SQLAlchemy ORM Models for Bifrost

Pure database models using SQLAlchemy 2.0 declarative style.
These models define the database schema and relationships.

For API schemas (Create/Update/Public), see schemas.py
"""

from src.models.orm.agent_prompt_history import AgentPromptHistory
from src.models.orm.agent_run_flag_conversations import AgentRunFlagConversation
from src.models.orm.agent_run_verdict_history import AgentRunVerdictHistory
from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.models.orm.agents import Agent, AgentDelegation, AgentRole, AgentTool, Conversation, Message
from src.models.orm.ai_usage import AIModelPricing, AIUsage
from src.models.orm.app_embed_secrets import AppEmbedSecret
from src.models.orm.form_embed_secrets import FormEmbedSecret
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.models.orm.audit import AuditLog
from src.models.orm.base import Base
from src.models.orm.branding import GlobalBranding
from src.models.orm.cli import CLISession
from src.models.orm.config import Config, SystemConfig
from src.models.orm.developer import DeveloperContext
from src.models.orm.events import Event, EventDelivery, EventSource, EventSubscription, WebhookSource
from src.models.orm.executions import Execution, ExecutionLog
from src.models.orm.external_mcp import (
    AgentMCPConnection,
    MCPConnection,
    MCPConnectionTool,
    MCPServer,
    UserMCPCredential,
)
from src.models.orm.forms import Form, FormField, FormRole
from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
from src.models.orm.knowledge import KnowledgeStore
from src.models.orm.knowledge_sources import KnowledgeNamespaceRole
from src.models.orm.metrics import ExecutionMetricsDaily, KnowledgeStorageDaily, PlatformMetricsSnapshot, WorkflowROIDaily
from src.models.orm.mfa import MFARecoveryCode, TrustedDevice, UserMFAMethod, UserOAuthAccount
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization
from src.models.orm.tables import Document, Table
from src.models.orm.users import Role, User, UserRole
from src.models.orm.user_invites import UserInvite
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.models.orm.file_index import FileIndex
from src.models.orm.worker_metric import WorkerMetric

__all__ = [
    # Base
    "Base",
    # Organizations
    "Organization",
    # Applications (App Builder)
    "Application",
    "AppEmbedSecret",
    "AppRole",
    # Users and Roles
    "User",
    "Role",
    "UserRole",
    "UserInvite",
    # Agent Runs
    "AgentRun",
    "AgentRunFlagConversation",
    "AgentRunStep",
    "AgentRunVerdictHistory",
    "SummaryBackfillJob",
    # Agents
    "Agent",
    "AgentPromptHistory",
    "AgentTool",
    "AgentDelegation",
    "AgentRole",
    "Conversation",
    "Message",
    # AI Usage
    "AIModelPricing",
    "AIUsage",
    # Forms
    "Form",
    "FormField",
    "FormRole",
    "FormEmbedSecret",
    # Executions
    "Execution",
    "ExecutionLog",
    # CLI Sessions
    "CLISession",
    # Config
    "Config",
    "SystemConfig",
    # Workflows
    "Workflow",
    "WorkflowRole",
    # OAuth
    "OAuthProvider",
    "OAuthToken",
    # Integrations
    "Integration",
    "IntegrationConfigSchema",
    "IntegrationMapping",
    # Knowledge Store
    "KnowledgeStore",
    # Knowledge Namespace Roles
    "KnowledgeNamespaceRole",
    # Audit
    "AuditLog",
    # MFA
    "UserMFAMethod",
    "MFARecoveryCode",
    "TrustedDevice",
    "UserOAuthAccount",
    # Branding
    "GlobalBranding",
    # Metrics
    "ExecutionMetricsDaily",
    "KnowledgeStorageDaily",
    "PlatformMetricsSnapshot",
    "WorkflowROIDaily",
    # Workspace
    "FileIndex",
    # Worker Metrics
    "WorkerMetric",
    # Developer
    "DeveloperContext",
    # Events
    "EventSource",
    "WebhookSource",
    "EventSubscription",
    "Event",
    "EventDelivery",
    # External MCP (client)
    "MCPServer",
    "MCPConnection",
    "MCPConnectionTool",
    "UserMCPCredential",
    "AgentMCPConnection",
    # Tables (App Builder)
    "Table",
    "Document",
]
