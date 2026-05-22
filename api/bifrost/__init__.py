"""
Bifrost Platform SDK

Provides Python API access to platform features from workflows.

All SDK methods are async and must be awaited.

Usage:
    from bifrost import organizations, workflows, files, forms, executions, roles
    from bifrost import config, integrations, ai, knowledge, tables

Example:
    # AI Completions
    response = await ai.complete("Summarize this text...")
    print(response.content)

    # Structured AI output with Pydantic
    from pydantic import BaseModel
    class Summary(BaseModel):
        title: str
        points: list[str]
    result = await ai.complete("Summarize...", response_format=Summary)
    print(result.title)  # Typed!

    # AI with RAG context
    response = await ai.complete(
        "What is our refund policy?",
        knowledge=["policies", "faq"]
    )

    # Streaming AI
    async for chunk in ai.stream("Write a story..."):
        print(chunk.content, end="")

    # Knowledge Store (RAG)
    await knowledge.store(
        "Our refund policy allows 30-day returns.",
        namespace="policies",
        key="refund-policy"
    )
    results = await knowledge.search("return policy", namespace="policies")
    for doc in results:
        print(doc.content, doc.score)

    # Create an organization
    org = await organizations.create("Acme Corp", domain="acme.com")

    # List workflows
    wf_list = await workflows.list()
    for wf in wf_list:
        print(wf.name, wf.description)

    # File operations (async)
    await files.write("data/temp.txt", "content", location="temp")
    data = await files.read("data/customers.csv", location="workspace")

    # List executions
    recent = await executions.list(limit=10)
    for execution in recent:
        print(f"{execution.workflow_name}: {execution.status}")

    # Create a role
    role = await roles.create("Manager", description="Can manage team data")

    # Manage configuration
    await config.set("api_url", "https://api.example.com")
    url = await config.get("api_url")
    cfg = await config.list()
    print(cfg.api_url)  # Dot notation access

    # Get integration with OAuth tokens
    integration = await integrations.get("HaloPSA")
    if integration and integration.oauth:
        client_id = integration.oauth.client_id
        refresh_token = integration.oauth.refresh_token
"""

# SDK Modules
# Most modules are imported eagerly — they share a common httpx dependency via .client.
# The ai module is lazy-loaded via __getattr__ because it pulls in openai/anthropic
# (~1,078 modules) which are only needed if a workflow calls ai.complete().
from .api import api
from .config import config
from .events import events
from .executions import executions
from .files import files
from .forms import forms
from .integrations import integrations
from .knowledge import knowledge
from .organizations import organizations
from .roles import roles
from .tables import tables
from .users import users
from .workflows import workflows
from .agents import agents

# SDK Models (single source of truth)
from .models import (
    Organization,
    Role,
    UserPublic,
    FormPublic,
    WorkflowMetadata,
    WorkflowExecution,
    IntegrationData,
    OAuthCredentials,
    IntegrationMappingResponse,
    ConfigData,
    AIResponse,
    AIStreamChunk,
    KnowledgeDocument,
    NamespaceInfo,
    TableInfo,
    DocumentData,
    DocumentList,
    BatchResult,
    BatchDeleteResult,
)

# ExecutionContext lives in bifrost/ — available in both CLI and platform
from ._execution_context import ExecutionContext

# Import decorators - try platform module first, fall back to local SDK version
try:
    from src.sdk.decorators import workflow, data_provider, tool
except ImportError:
    # CLI/standalone mode - use local decorators
    from .decorators import workflow, data_provider, tool
    _ = WorkflowMetadata  # noqa: F401 - re-exported from .models above

# Import context proxy for accessing ExecutionContext without parameter
from ._context import context

# SDK Errors - try platform module first, fall back to local definitions
try:
    from src.sdk.errors import UserError, WorkflowError, ValidationError, IntegrationError, ConfigurationError  # type: ignore[assignment]
except ImportError:
    # CLI/standalone mode - define minimal error classes
    class UserError(Exception):  # type: ignore[no-redef]
        """User-facing error with formatted message."""
        pass

    class WorkflowError(Exception):  # type: ignore[no-redef]
        """Workflow execution error."""
        pass

    class ValidationError(Exception):  # type: ignore[no-redef]
        """Validation error."""
        pass

    class IntegrationError(Exception):  # type: ignore[no-redef]
        """Integration error."""
        pass

    class ConfigurationError(Exception):  # type: ignore[no-redef]
        """Configuration error."""
        pass

# Enums - try platform module first, fall back to local definitions
# NOTE: We load src/models/enums.py directly via spec_from_file_location instead of
# `from src.models.enums import ...` because the normal import triggers
# src/models/__init__.py which eagerly loads ALL ORM models → sqlalchemy → fastapi
# (~200 modules, ~50MB). Worker subprocesses never need sqlalchemy.
try:
    import importlib.util as _imputil
    from pathlib import Path as _Path
    _enums_path = _Path(__file__).parent.parent / "src" / "models" / "enums.py"
    _spec = _imputil.spec_from_file_location("_bifrost_enums", str(_enums_path))
    if _spec and _spec.loader:
        _enums_mod = _imputil.module_from_spec(_spec)
        _spec.loader.exec_module(_enums_mod)
        ExecutionStatus = _enums_mod.ExecutionStatus  # type: ignore[assignment,misc]
        ConfigType = _enums_mod.ConfigType  # type: ignore[assignment,misc]
        FormFieldType = _enums_mod.FormFieldType  # type: ignore[assignment,misc]
        del _enums_mod
    else:
        raise ImportError("Could not load enums")
    del _spec, _enums_path, _Path, _imputil
except (ImportError, ModuleNotFoundError, OSError):
    from enum import Enum

    class ExecutionStatus(str, Enum):  # type: ignore[no-redef]
        """Workflow execution status."""
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class ConfigType(str, Enum):  # type: ignore[no-redef]
        """Configuration value type."""
        STRING = "string"
        SECRET = "secret"
        JSON = "json"
        BOOL = "bool"
        INT = "int"

    class FormFieldType(str, Enum):  # type: ignore[no-redef]
        """Form field type."""
        TEXT = "text"
        NUMBER = "number"
        SELECT = "select"
        CHECKBOX = "checkbox"
        DATE = "date"

# Lazy-load the ai module on first access.
# bifrost.ai imports openai + anthropic (~1,078 modules, ~100MB) which are only
# needed if a workflow calls ai.complete(). Lazy loading means lightweight
# workflows never pay this cost.
def __getattr__(name: str):
    if name == "ai":
        from .ai import ai
        globals()["ai"] = ai  # Cache so __getattr__ is not called again
        return ai
    raise AttributeError(f"module 'bifrost' has no attribute {name!r}")


__all__ = [
    # SDK Modules
    'agents',
    'api',
    'ai',
    'config',
    'events',
    'executions',
    'files',
    'forms',
    'integrations',
    'knowledge',
    'organizations',
    'roles',
    'tables',
    'users',
    'workflows',
    # SDK Models
    'Organization',
    'Role',
    'UserPublic',
    'FormPublic',
    'WorkflowMetadata',
    'WorkflowExecution',
    'IntegrationData',
    'OAuthCredentials',
    'IntegrationMappingResponse',
    'ConfigData',
    'AIResponse',
    'AIStreamChunk',
    'KnowledgeDocument',
    'NamespaceInfo',
    'TableInfo',
    'DocumentData',
    'DocumentList',
    'BatchResult',
    'BatchDeleteResult',
    # Decorators
    'workflow',
    'data_provider',
    'tool',
    # Context
    'context',
    'ExecutionContext',
    # Enums
    'ExecutionStatus',
    'ConfigType',
    'FormFieldType',
    # Errors
    'UserError',
    'WorkflowError',
    'ValidationError',
    'IntegrationError',
    'ConfigurationError',
]

import os as _os  # noqa: E402  -- kept below __all__ so it isn't re-exported
import subprocess as _subprocess  # noqa: E402


def _compute_version() -> str:
    if v := _os.environ.get("BIFROST_VERSION"):
        return v
    try:
        return _subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            text=True,
            stderr=_subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


__version__ = _compute_version()
