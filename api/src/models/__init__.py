"""
Bifrost Models

ORM models (database tables):
    from src.models import Organization, User, Form
    from src.models.orm import Organization, User, Form
    from src.models.orm.users import User  # Granular access

Pydantic contracts (API request/response):
    from src.models import OrganizationCreate, OrganizationPublic
    from src.models.contracts import OrganizationCreate, OrganizationPublic
    from src.models.contracts.users import UserCreate  # Granular access

Enums:
    from src.models import ExecutionStatus
    from src.models.enums import ExecutionStatus
"""

# ORM models (database tables)
from src.models.orm import (
    Base,
    Organization,
    Solution,
    User,
    Role,
    UserRole,
    Form,
    FormField,
    FormRole,
    Agent,
    AgentTool,
    AgentDelegation,
    AgentRole,
    Conversation,
    Message,
    AIModelPricing,
    AIUsage,
    Execution,
    ExecutionLog,
    CLISession,
    Config,
    Workflow,
    OAuthProvider,
    OAuthToken,
    Integration,
    IntegrationMapping,
    AuditLog,
    UserMFAMethod,
    MFARecoveryCode,
    TrustedDevice,
    UserOAuthAccount,
    SystemConfig,
    GlobalBranding,
    ExecutionMetricsDaily,
    PlatformMetricsSnapshot,
    WorkflowROIDaily,
    FileIndex,
    # Applications (App Builder)
    Application,
    PlatformJob,
    AppRole,
)

# Pydantic schemas (API request/response) - from contracts/
# Re-export everything from contracts (conflicting names removed from contracts/__all__)
from src.models.contracts import *  # noqa: F401, F403

# Enums
from src.models.enums import (
    ExecutionStatus,
    FormAccessLevel,
    FormFieldType,
    ConfigType,
    MFAMethodType,
    MFAMethodStatus,
)

# Import __all__ from contracts for completeness
from src.models.contracts import __all__ as _contracts_all

# Combine all exports
__all__ = [
    # Base
    "Base",
    # ORM models
    "Organization",
    "Solution",
    "User",
    "Role",
    "UserRole",
    "Form",
    "FormField",
    "FormRole",
    "Agent",
    "AgentTool",
    "AgentDelegation",
    "AgentRole",
    "Conversation",
    "Message",
    "AIModelPricing",
    "AIUsage",
    "Execution",
    "ExecutionLog",
    "CLISession",
    "Config",
    "Workflow",
    "OAuthProvider",
    "OAuthToken",
    "Integration",
    "IntegrationMapping",
    "AuditLog",
    "UserMFAMethod",
    "MFARecoveryCode",
    "TrustedDevice",
    "UserOAuthAccount",
    "SystemConfig",
    "GlobalBranding",
    "ExecutionMetricsDaily",
    "PlatformMetricsSnapshot",
    "WorkflowROIDaily",
    "FileIndex",
    # Applications (App Builder)
    "Application",
    "PlatformJob",
    "AppRole",
    # Enums
    "ExecutionStatus",
    "FormAccessLevel",
    "FormFieldType",
    "ConfigType",
    "MFAMethodType",
    "MFAMethodStatus",
] + list(_contracts_all)
