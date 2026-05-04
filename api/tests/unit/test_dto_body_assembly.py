"""Round-trip tests for :func:`bifrost.dto_flags.assemble_body`.

Each test case feeds parsed CLI args (mirroring what Click would produce)
through ``assemble_body`` with a stub :class:`RefResolver` and asserts the
REST payload matches expectations. Covers:

- ``None`` / empty-tuple omit-unset semantics.
- ``role_ids`` / repeatable-list flag handling, including comma-split.
- Ref resolution for ``workflow_id``, ``organization_id``, ``application_id``.
- ``config_type → type`` rename for ``ConfigCreate`` / ``ConfigUpdate``.
- ``@file`` loader and inline JSON for ``dict`` fields.
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

# Standalone bifrost SDK package import.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pytest

from bifrost.dto_flags import (  # noqa: E402
    DTO_EXCLUDES,
    DTO_FIELD_ALIASES,
    DTO_REF_LOOKUPS,
    assemble_body,
    load_dict_value,
)
from src.models.contracts.agents import AgentCreate, AgentUpdate  # noqa: E402
from src.models.contracts.applications import (  # noqa: E402
    ApplicationCreate,
    ApplicationUpdate,
)
from src.models.contracts.config import ConfigCreate, ConfigUpdate  # noqa: E402
from src.models.contracts.events import (  # noqa: E402
    EventSourceCreate,
    EventSubscriptionCreate,
)
from src.models.contracts.forms import FormCreate, FormUpdate  # noqa: E402
from src.models.contracts.integrations import (  # noqa: E402
    IntegrationCreate,
    IntegrationMappingCreate,
    IntegrationUpdate,
)
from src.models.contracts.organizations import (  # noqa: E402
    OrganizationCreate,
    OrganizationUpdate,
)
from src.models.contracts.tables import TableCreate  # noqa: E402
from src.models.contracts.users import RoleCreate, RoleUpdate  # noqa: E402
from src.models.contracts.workflows import WorkflowUpdateRequest  # noqa: E402


class FakeResolver:
    """Stub ``RefResolver`` that maps ``(kind, value) → uuid`` from a dict."""

    def __init__(self, mapping: dict[tuple[str, str], str]) -> None:
        self._mapping = mapping
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, kind: str, value: str) -> str:
        self.calls.append((kind, value))
        try:
            return self._mapping[(kind, value)]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AssertionError(
                f"FakeResolver missing mapping for ({kind!r}, {value!r})"
            ) from exc


WORKFLOW_UUID = "11111111-1111-1111-1111-111111111111"
LAUNCH_WORKFLOW_UUID = "22222222-2222-2222-2222-222222222222"
ORG_UUID = "33333333-3333-3333-3333-333333333333"
APP_UUID = "44444444-4444-4444-4444-444444444444"
ROLE_UUID = "55555555-5555-5555-5555-555555555555"
ROLE_UUID_2 = "66666666-6666-6666-6666-666666666666"
AGENT_UUID = "77777777-7777-7777-7777-777777777777"


def _resolver() -> FakeResolver:
    return FakeResolver(
        {
            ("workflow", "MyWorkflow"): WORKFLOW_UUID,
            ("workflow", "Launcher"): LAUNCH_WORKFLOW_UUID,
            ("workflow", WORKFLOW_UUID): WORKFLOW_UUID,
            ("org", "Acme"): ORG_UUID,
            ("app", "my-app"): APP_UUID,
            ("role", "admin"): ROLE_UUID,
            ("role", "ops"): ROLE_UUID_2,
            ("agent", "Helper"): AGENT_UUID,
        }
    )


def _parsed(model_cls: type, values: dict[str, Any]) -> dict[str, Any]:
    """Mirror Click's behaviour: any field absent from ``values`` becomes ``None``.

    Repeatable list flags get an empty tuple when not passed.
    """
    parsed: dict[str, Any] = {}
    excludes = DTO_EXCLUDES.get(model_cls.__name__, set())
    refs = DTO_REF_LOOKUPS.get(model_cls.__name__, {})
    for name, field in model_cls.model_fields.items():
        if name in excludes:
            continue
        if name in values:
            parsed[name] = values[name]
            continue
        # Default for unset Click flag.
        if name in refs:
            parsed[name] = None
        else:
            anno = field.annotation
            origin = getattr(anno, "__origin__", None)
            if origin is list or anno is list:
                parsed[name] = ()
            else:
                parsed[name] = None
    return parsed


# ---------------------------------------------------------------------------
# Omit-unset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_omit_unset_drops_none_and_empty_tuples() -> None:
    resolver = _resolver()
    body = await assemble_body(
        OrganizationUpdate,
        _parsed(OrganizationUpdate, {"name": "NewName"}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"name": "NewName"}


# ---------------------------------------------------------------------------
# Renames
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_create_renames_config_type_to_type() -> None:
    resolver = _resolver()
    body = await assemble_body(
        ConfigCreate,
        _parsed(
            ConfigCreate,
            {
                "key": "MY_KEY",
                "value": '{"raw": 1}',
                "config_type": "string",
                "organization_id": "Acme",
            },
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["type"] == "string"
    assert "config_type" not in body
    assert body["organization_id"] == ORG_UUID
    assert body["key"] == "MY_KEY"
    assert DTO_FIELD_ALIASES["ConfigCreate"] == {"config_type": "type"}


@pytest.mark.asyncio
async def test_config_update_rename_applies() -> None:
    resolver = _resolver()
    body = await assemble_body(
        ConfigUpdate,
        _parsed(ConfigUpdate, {"config_type": "int"}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"type": "int"}


@pytest.mark.asyncio
async def test_per_call_field_aliases_merge_with_registry() -> None:
    resolver = _resolver()
    body = await assemble_body(
        RoleCreate,
        _parsed(RoleCreate, {"name": "admin"}),
        resolver=resolver,  # type: ignore[arg-type]
        field_aliases={"name": "role_name"},
    )
    assert body == {"role_name": "admin"}


# ---------------------------------------------------------------------------
# Ref resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_form_create_resolves_workflow_and_org() -> None:
    resolver = _resolver()
    body = await assemble_body(
        FormCreate,
        _parsed(
            FormCreate,
            {
                "name": "Onboarding",
                "workflow_id": "MyWorkflow",
                "launch_workflow_id": "Launcher",
                "organization_id": "Acme",
                "form_schema": '{"fields":[]}',
            },
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["workflow_id"] == WORKFLOW_UUID
    assert body["launch_workflow_id"] == LAUNCH_WORKFLOW_UUID
    assert body["organization_id"] == ORG_UUID
    assert body["form_schema"] == {"fields": []}
    assert ("workflow", "MyWorkflow") in resolver.calls
    assert ("workflow", "Launcher") in resolver.calls


@pytest.mark.asyncio
async def test_uuid_passthrough_for_ref_fields() -> None:
    resolver = _resolver()
    body = await assemble_body(
        FormUpdate,
        _parsed(FormUpdate, {"workflow_id": WORKFLOW_UUID}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"workflow_id": WORKFLOW_UUID}


@pytest.mark.asyncio
async def test_event_subscription_resolves_workflow_or_agent() -> None:
    resolver = _resolver()
    body = await assemble_body(
        EventSubscriptionCreate,
        _parsed(
            EventSubscriptionCreate,
            {"target_type": "agent", "agent_id": "Helper"},
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["agent_id"] == AGENT_UUID
    assert body["target_type"] == "agent"
    assert "workflow_id" not in body


# ---------------------------------------------------------------------------
# List handling (repeatable + comma-split + role resolution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_ids_resolve_names_to_uuids() -> None:
    resolver = _resolver()
    body = await assemble_body(
        AgentCreate,
        _parsed(
            AgentCreate,
            {
                "name": "Bot",
                "system_prompt": "Hi",
                "role_ids": ("admin", "ops"),
            },
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["role_ids"] == [ROLE_UUID, ROLE_UUID_2]


@pytest.mark.asyncio
async def test_role_ids_comma_split_accepted() -> None:
    resolver = _resolver()
    body = await assemble_body(
        AgentUpdate,
        _parsed(AgentUpdate, {"role_ids": ("admin,ops",)}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["role_ids"] == [ROLE_UUID, ROLE_UUID_2]


@pytest.mark.asyncio
async def test_form_create_resolves_role_ids() -> None:
    """``--role-ids admin,ops`` on form create resolves names → UUIDs."""
    resolver = _resolver()
    body = await assemble_body(
        FormCreate,
        _parsed(
            FormCreate,
            {
                "name": "F",
                "workflow_id": "MyWorkflow",
                "form_schema": '{"fields":[]}',
                "role_ids": ("admin", "ops"),
            },
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["role_ids"] == [ROLE_UUID, ROLE_UUID_2]


@pytest.mark.asyncio
async def test_form_update_resolves_role_ids_comma_split() -> None:
    resolver = _resolver()
    body = await assemble_body(
        FormUpdate,
        _parsed(FormUpdate, {"role_ids": ("admin,ops",)}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["role_ids"] == [ROLE_UUID, ROLE_UUID_2]


@pytest.mark.asyncio
async def test_form_update_omits_role_ids_when_unset() -> None:
    """No ``--role-ids`` → field absent from body (not ``[]``)."""
    resolver = _resolver()
    body = await assemble_body(
        FormUpdate,
        _parsed(FormUpdate, {"name": "Renamed"}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert "role_ids" not in body
    assert body == {"name": "Renamed"}


@pytest.mark.asyncio
async def test_workflow_update_resolves_role_ids() -> None:
    resolver = _resolver()
    body = await assemble_body(
        WorkflowUpdateRequest,
        _parsed(WorkflowUpdateRequest, {"role_ids": ("admin", "ops")}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["role_ids"] == [ROLE_UUID, ROLE_UUID_2]


@pytest.mark.asyncio
async def test_repeatable_list_str_passthrough() -> None:
    resolver = _resolver()
    body = await assemble_body(
        WorkflowUpdateRequest,
        _parsed(WorkflowUpdateRequest, {"tags": ("alpha", "beta")}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"tags": ["alpha", "beta"]}


# ---------------------------------------------------------------------------
# Dict / @file handling
# ---------------------------------------------------------------------------


def test_load_dict_value_inline_json() -> None:
    assert load_dict_value('{"a": 1}') == {"a": 1}


def test_load_dict_value_at_file_yaml(tmp_path: pathlib.Path) -> None:
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text("foo: bar\nlist:\n  - 1\n  - 2\n", encoding="utf-8")
    assert load_dict_value(f"@{schema_path}") == {"foo": "bar", "list": [1, 2]}


def test_load_dict_value_at_file_json(tmp_path: pathlib.Path) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps({"x": True}), encoding="utf-8")
    assert load_dict_value(f"@{schema_path}") == {"x": True}


def test_load_dict_value_none() -> None:
    assert load_dict_value(None) is None


@pytest.mark.asyncio
async def test_dict_field_loaded_from_at_file(tmp_path: pathlib.Path) -> None:
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text("fields: []\n", encoding="utf-8")
    resolver = _resolver()
    body = await assemble_body(
        FormCreate,
        _parsed(
            FormCreate,
            {
                "name": "F",
                "workflow_id": "MyWorkflow",
                "form_schema": f"@{schema_path}",
            },
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["form_schema"] == {"fields": []}


# ---------------------------------------------------------------------------
# Coverage smoke tests for remaining DTOs (each must round-trip cleanly)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_organization_create_round_trip() -> None:
    resolver = _resolver()
    body = await assemble_body(
        OrganizationCreate,
        _parsed(OrganizationCreate, {"name": "Acme", "is_active": True}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"name": "Acme", "is_active": True}


@pytest.mark.asyncio
async def test_role_update_omit_unset() -> None:
    resolver = _resolver()
    body = await assemble_body(
        RoleUpdate,
        _parsed(RoleUpdate, {"description": "ops team"}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"description": "ops team"}


@pytest.mark.asyncio
async def test_application_create_resolves_org_only() -> None:
    resolver = _resolver()
    body = await assemble_body(
        ApplicationCreate,
        _parsed(
            ApplicationCreate,
            {
                "name": "App",
                "slug": "app",
                "organization_id": "Acme",
                "access_level": "authenticated",
            },
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["organization_id"] == ORG_UUID
    assert body["slug"] == "app"
    assert body["access_level"] == "authenticated"


@pytest.mark.asyncio
async def test_application_update_no_ref_resolution_for_scope() -> None:
    """ApplicationUpdate's ``scope`` is free-form (``global`` or org UUID)."""
    resolver = _resolver()
    body = await assemble_body(
        ApplicationUpdate,
        _parsed(ApplicationUpdate, {"scope": "global", "name": "Renamed"}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"scope": "global", "name": "Renamed"}


@pytest.mark.asyncio
async def test_integration_create_excludes_oauth_provider_guard() -> None:
    """``oauth_provider`` exclude is a no-op today (field doesn't exist) but
    must not break basic assembly."""
    resolver = _resolver()
    body = await assemble_body(
        IntegrationCreate,
        _parsed(IntegrationCreate, {"name": "Halo"}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"name": "Halo"}


@pytest.mark.asyncio
async def test_integration_update_resolves_data_provider_via_workflow_kind() -> None:
    resolver = _resolver()
    body = await assemble_body(
        IntegrationUpdate,
        _parsed(
            IntegrationUpdate,
            {"list_entities_data_provider_id": "MyWorkflow"},
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"list_entities_data_provider_id": WORKFLOW_UUID}


@pytest.mark.asyncio
async def test_integration_mapping_create_resolves_org() -> None:
    resolver = _resolver()
    body = await assemble_body(
        IntegrationMappingCreate,
        _parsed(
            IntegrationMappingCreate,
            {"organization_id": "Acme", "entity_id": "tenant-xyz"},
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body == {"organization_id": ORG_UUID, "entity_id": "tenant-xyz"}


@pytest.mark.asyncio
async def test_table_create_resolves_org() -> None:
    resolver = _resolver()
    body = await assemble_body(
        TableCreate,
        _parsed(TableCreate, {"name": "tickets", "organization_id": "Acme"}),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["organization_id"] == ORG_UUID
    assert body["name"] == "tickets"


@pytest.mark.asyncio
async def test_event_source_create_round_trip() -> None:
    resolver = _resolver()
    body = await assemble_body(
        EventSourceCreate,
        _parsed(
            EventSourceCreate,
            {
                "name": "Schedule",
                "source_type": "schedule",
                "organization_id": "Acme",
            },
        ),
        resolver=resolver,  # type: ignore[arg-type]
    )
    assert body["name"] == "Schedule"
    assert body["organization_id"] == ORG_UUID
    assert body["source_type"] == "schedule"
