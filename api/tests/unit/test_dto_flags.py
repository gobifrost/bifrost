"""Field-parity tests for ``bifrost.dto_flags``.

For every CRUD DTO we expose via the CLI / MCP surface, assert that
:func:`build_cli_flags` produces a flag for every non-excluded writable
field. When a DTO grows a new field, this test fails loudly so the new
surface is either exposed or documented in
:data:`bifrost.dto_flags.DTO_EXCLUDES`.
"""
from __future__ import annotations

import pathlib
import sys

# Standalone bifrost SDK package import (mirrors test_cli_migrate_imports).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pytest

from bifrost.dto_flags import (  # noqa: E402
    DTO_EXCLUDES,
    DTO_REF_LOOKUPS,
    build_cli_flags,
)
from bifrost.contracts.claims import CustomClaimCreate, CustomClaimUpdate  # noqa: E402
from src.models.contracts.agents import AgentCreate, AgentUpdate  # noqa: E402
from src.models.contracts.applications import (  # noqa: E402
    ApplicationCreate,
    ApplicationUpdate,
)
from src.models.contracts.config import ConfigCreate, ConfigUpdate  # noqa: E402
from src.models.contracts.events import (  # noqa: E402
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
)
from src.models.contracts.forms import FormCreate, FormUpdate  # noqa: E402
from src.models.contracts.integrations import (  # noqa: E402
    IntegrationCreate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    IntegrationUpdate,
)
from src.models.contracts.organizations import (  # noqa: E402
    OrganizationCreate,
    OrganizationUpdate,
)
from src.models.contracts.tables import TableCreate, TableUpdate  # noqa: E402
from src.models.contracts.users import RoleCreate, RoleUpdate  # noqa: E402
from src.models.contracts.workflows import WorkflowUpdateRequest  # noqa: E402

# DTOs covered by the field-parity contract. Each entry maps a
# Pydantic model class to its declared exclude set; a missing entry means
# "exclude nothing." Workflows have no ``Create`` DTO — workflows are
# created from code via @workflow registration, not the API.
COVERED_DTOS: list[type] = [
    OrganizationCreate,
    OrganizationUpdate,
    RoleCreate,
    RoleUpdate,
    WorkflowUpdateRequest,
    FormCreate,
    FormUpdate,
    AgentCreate,
    AgentUpdate,
    ApplicationCreate,
    ApplicationUpdate,
    IntegrationCreate,
    IntegrationUpdate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    ConfigCreate,
    ConfigUpdate,
    CustomClaimCreate,
    CustomClaimUpdate,
    TableCreate,
    TableUpdate,
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
]


def _flag_field_names(model_cls: type) -> set[str]:
    """Inspect generated Click options and return the destination param names."""
    excludes = DTO_EXCLUDES.get(model_cls.__name__, set())
    refs = DTO_REF_LOOKUPS.get(model_cls.__name__, {})
    decorators = build_cli_flags(model_cls, exclude=excludes, verb_ref_lookups=refs)

    # Each decorator is ``click.option(...)``; apply to a stub fn to read params.
    @decorators[0]  # type: ignore[misc]
    def _stub() -> None:  # pragma: no cover - introspection helper
        return None

    fn = _stub
    for dec in decorators[1:]:
        fn = dec(fn)

    return {param.name for param in fn.__click_params__}  # type: ignore[attr-defined]


@pytest.mark.parametrize("model_cls", COVERED_DTOS, ids=lambda c: c.__name__)
def test_field_parity(model_cls: type) -> None:
    """Every non-excluded DTO field must appear as a generated flag."""
    excludes = DTO_EXCLUDES.get(model_cls.__name__, set())
    declared = set(model_cls.model_fields)
    expected = declared - excludes
    actual = _flag_field_names(model_cls)

    missing = expected - actual
    extra = actual - expected
    assert not missing and not extra, (
        f"DTO {model_cls.__name__} field-parity drift detected.\n"
        f"  declared fields: {sorted(declared)}\n"
        f"  excluded:        {sorted(excludes)}\n"
        f"  expected flags:  {sorted(expected)}\n"
        f"  generated flags: {sorted(actual)}\n"
        f"  missing flags:   {sorted(missing)}\n"
        f"  extra flags:     {sorted(extra)}\n"
        f"Either expose the new field as a flag or add it to "
        f"DTO_EXCLUDES['{model_cls.__name__}'] with a one-line reason."
    )


@pytest.mark.parametrize("model_cls", COVERED_DTOS, ids=lambda c: c.__name__)
def test_excludes_are_real_fields(model_cls: type) -> None:
    """Every entry in DTO_EXCLUDES must correspond to a real field.

    Catches drift the other way — a field gets removed but the exclude entry
    is left behind. ``oauth_provider`` on integrations is an exception: the
    plan declares it as an out-of-scope guardrail even though the field
    doesn't exist on the DTO yet, so it short-circuits the contract.
    """
    excludes = DTO_EXCLUDES.get(model_cls.__name__, set())
    declared = set(model_cls.model_fields)
    stale = excludes - declared - {"oauth_provider"}
    assert not stale, (
        f"DTO_EXCLUDES['{model_cls.__name__}'] contains stale entries that no "
        f"longer correspond to a real field: {sorted(stale)}. Remove them."
    )


def test_ref_lookup_flag_naming() -> None:
    """``workflow_id`` → ``--workflow``; paired refs keep the disambiguator."""
    decorators = build_cli_flags(
        FormCreate,
        exclude=DTO_EXCLUDES.get("FormCreate", set()),
        verb_ref_lookups=DTO_REF_LOOKUPS["FormCreate"],
    )

    @decorators[0]  # type: ignore[misc]
    def _stub() -> None:  # pragma: no cover - introspection helper
        return None

    fn = _stub
    for dec in decorators[1:]:
        fn = dec(fn)

    flag_to_dest = {
        "/".join(p.opts): p.name  # type: ignore[attr-defined]
        for p in fn.__click_params__  # type: ignore[attr-defined]
    }
    # Single-arm workflow ref → bare ``--workflow``.
    assert flag_to_dest.get("--workflow") == "workflow_id"
    # Paired ref keeps the field stem so the two flags don't collide.
    assert flag_to_dest.get("--launch-workflow") == "launch_workflow_id"
    # Org ref strips ``_id`` cleanly.
    assert flag_to_dest.get("--organization") == "organization_id"
