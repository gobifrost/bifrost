"""Field-parity tests between CLI-side DTO mirrors and server-side DTOs.

``api/bifrost/contracts/`` holds minimal Pydantic mirrors of the server
``XxxCreate`` / ``XxxUpdate`` DTOs so the downloadable CLI tarball is
self-contained (see ``bifrost/contracts/__init__.py`` for rationale).

This test enforces that each mirror's ``model_fields`` set matches the
server DTO's. If a server field is added or renamed without updating the
mirror, the next CLI release would silently drop the new flag — this test
fails loudly instead.

Enum value-set parity is also checked so a new enum member on the server
(e.g., a new ``AgentChannel``) surfaces as a CLI drift failure.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

# Standalone bifrost SDK package import (mirrors test_cli_migrate_imports).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pytest

from bifrost import contracts as cli_contracts  # noqa: E402
from bifrost.contracts import enums as cli_enums  # noqa: E402
from src.models import enums as server_enums  # noqa: E402
from src.models.contracts import agents as server_agents  # noqa: E402
from src.models.contracts import applications as server_applications  # noqa: E402
from src.models.contracts import claims as server_claims  # noqa: E402
from src.models.contracts import config as server_config  # noqa: E402
from src.models.contracts import events as server_events  # noqa: E402
from src.models.contracts import forms as server_forms  # noqa: E402
from src.models.contracts import integrations as server_integrations  # noqa: E402
from src.models.contracts import organizations as server_organizations  # noqa: E402
from src.models.contracts import tables as server_tables  # noqa: E402
from src.models.contracts import users as server_users  # noqa: E402
from src.models.contracts import workflows as server_workflows  # noqa: E402


# Mirror class → server class. Each pair must have identical ``model_fields``.
DTO_PAIRS: list[tuple[type, type]] = [
    (cli_contracts.OrganizationCreate, server_organizations.OrganizationCreate),
    (cli_contracts.OrganizationUpdate, server_organizations.OrganizationUpdate),
    (cli_contracts.RoleCreate, server_users.RoleCreate),
    (cli_contracts.RoleUpdate, server_users.RoleUpdate),
    (cli_contracts.WorkflowUpdateRequest, server_workflows.WorkflowUpdateRequest),
    (cli_contracts.FormCreate, server_forms.FormCreate),
    (cli_contracts.FormUpdate, server_forms.FormUpdate),
    (cli_contracts.AgentCreate, server_agents.AgentCreate),
    (cli_contracts.AgentUpdate, server_agents.AgentUpdate),
    (cli_contracts.ApplicationCreate, server_applications.ApplicationCreate),
    (cli_contracts.ApplicationUpdate, server_applications.ApplicationUpdate),
    (cli_contracts.IntegrationCreate, server_integrations.IntegrationCreate),
    (cli_contracts.IntegrationUpdate, server_integrations.IntegrationUpdate),
    (
        cli_contracts.IntegrationMappingCreate,
        server_integrations.IntegrationMappingCreate,
    ),
    (
        cli_contracts.IntegrationMappingUpdate,
        server_integrations.IntegrationMappingUpdate,
    ),
    (cli_contracts.ConfigCreate, server_config.ConfigCreate),
    (cli_contracts.ConfigUpdate, server_config.ConfigUpdate),
    (cli_contracts.CustomClaimCreate, server_claims.CustomClaimCreate),
    (cli_contracts.CustomClaimUpdate, server_claims.CustomClaimUpdate),
    (cli_contracts.TableCreate, server_tables.TableCreate),
    (cli_contracts.TableUpdate, server_tables.TableUpdate),
    (cli_contracts.EventSourceCreate, server_events.EventSourceCreate),
    (cli_contracts.EventSourceUpdate, server_events.EventSourceUpdate),
    (
        cli_contracts.EventSubscriptionCreate,
        server_events.EventSubscriptionCreate,
    ),
    (
        cli_contracts.EventSubscriptionUpdate,
        server_events.EventSubscriptionUpdate,
    ),
]


ENUM_PAIRS: list[tuple[type, type]] = [
    (cli_enums.FormAccessLevel, server_enums.FormAccessLevel),
    (cli_enums.AgentAccessLevel, server_enums.AgentAccessLevel),
    (cli_enums.AgentChannel, server_enums.AgentChannel),
    (cli_enums.ConfigType, server_enums.ConfigType),
    (cli_enums.EventSourceType, server_enums.EventSourceType),
]


@pytest.mark.parametrize(
    "cli_cls,server_cls",
    DTO_PAIRS,
    ids=lambda c: c.__name__ if isinstance(c, type) else str(c),
)
def test_dto_field_parity(cli_cls: type, server_cls: type) -> None:
    """Mirror and server DTO must declare the same writable fields."""
    cli_fields = set(cli_cls.model_fields)
    server_fields = set(server_cls.model_fields)

    missing = server_fields - cli_fields
    extra = cli_fields - server_fields
    assert not missing and not extra, (
        f"CLI mirror {cli_cls.__module__}.{cli_cls.__name__} has drifted from "
        f"server {server_cls.__module__}.{server_cls.__name__}.\n"
        f"  missing from mirror: {sorted(missing)}\n"
        f"  extra in mirror:     {sorted(extra)}\n"
        f"Update api/bifrost/contracts/ to match."
    )


@pytest.mark.parametrize(
    "cli_enum,server_enum",
    ENUM_PAIRS,
    ids=lambda e: e.__name__ if isinstance(e, type) else str(e),
)
def test_enum_value_parity(cli_enum: Any, server_enum: Any) -> None:
    """Mirror and server enum must declare the same value set."""
    cli_values = {m.value for m in cli_enum}
    server_values = {m.value for m in server_enum}

    missing = server_values - cli_values
    extra = cli_values - server_values
    assert not missing and not extra, (
        f"CLI enum {cli_enum.__module__}.{cli_enum.__name__} has drifted from "
        f"server {server_enum.__module__}.{server_enum.__name__}.\n"
        f"  missing from mirror: {sorted(missing)}\n"
        f"  extra in mirror:     {sorted(extra)}\n"
        f"Update api/bifrost/contracts/enums.py to match."
    )
