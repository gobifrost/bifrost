"""Self-contained DTO mirrors for the CLI surface.

The CLI's entity commands (``bifrost/commands/*.py``) build flags and
request bodies from Pydantic ``XxxCreate`` / ``XxxUpdate`` DTOs via
``bifrost.dto_flags.build_cli_flags`` / ``assemble_body``. Those DTOs
originally live in ``src.models.contracts.*`` on the server side, but the
CLI tarball shipped by ``/api/cli/download`` does **not** include ``src/``
— so a fresh ``pip install`` of that tarball produced a CLI where every
``bifrost <entity> <verb>`` crashed with ``ModuleNotFoundError: No module
named 'src'``.

Fix: mirror the subset of DTOs the CLI needs here, inside the distributed
``bifrost`` package. The CLI imports from ``bifrost.contracts.*`` and the
tarball is fully self-contained. Drift between these local mirrors and
the server-side DTOs is caught by
``api/tests/unit/test_contracts_parity.py``, which compares
``model_fields`` sets and enum values between the two.

Scope: only fields needed for CLI flag introspection are mirrored. No
validators, serialisers, or field constraints — the server validates
request bodies with its own DTOs, so the mirror can be minimal.
"""

from bifrost.contracts.agents import AgentCreate, AgentUpdate
from bifrost.contracts.applications import ApplicationCreate, ApplicationUpdate
from bifrost.contracts.claims import CustomClaimCreate, CustomClaimUpdate
from bifrost.contracts.config import ConfigCreate, ConfigUpdate
from bifrost.contracts.enums import (
    AgentAccessLevel,
    AgentChannel,
    ConfigType,
    EventSourceType,
    FormAccessLevel,
)
from bifrost.contracts.events import (
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
    ScheduleSourceConfig,
    WebhookSourceConfig,
)
from bifrost.contracts.forms import FormCreate, FormUpdate
from bifrost.contracts.integrations import (
    ConfigSchemaItem,
    IntegrationCreate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    IntegrationUpdate,
)
from bifrost.contracts.organizations import OrganizationCreate, OrganizationUpdate
from bifrost.contracts.tables import TableCreate, TableUpdate
from bifrost.contracts.users import RoleCreate, RoleUpdate
from bifrost.contracts.workflows import WorkflowUpdateRequest

__all__ = [
    # Enums
    "AgentAccessLevel",
    "AgentChannel",
    "ConfigType",
    "EventSourceType",
    "FormAccessLevel",
    # Organizations
    "OrganizationCreate",
    "OrganizationUpdate",
    # Roles / users
    "RoleCreate",
    "RoleUpdate",
    # Workflows
    "WorkflowUpdateRequest",
    # Forms
    "FormCreate",
    "FormUpdate",
    # Agents
    "AgentCreate",
    "AgentUpdate",
    # Applications
    "ApplicationCreate",
    "ApplicationUpdate",
    # Integrations
    "ConfigSchemaItem",
    "IntegrationCreate",
    "IntegrationUpdate",
    "IntegrationMappingCreate",
    "IntegrationMappingUpdate",
    # Configs
    "ConfigCreate",
    "ConfigUpdate",
    # Custom Claims
    "CustomClaimCreate",
    "CustomClaimUpdate",
    # Tables
    "TableCreate",
    "TableUpdate",
    # Events
    "EventSourceCreate",
    "EventSourceUpdate",
    "EventSubscriptionCreate",
    "EventSubscriptionUpdate",
    "ScheduleSourceConfig",
    "WebhookSourceConfig",
]
