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
from bifrost.contracts.files import FilePolicyCreate, FilePolicyUpdate  # noqa: E402
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
    FilePolicyCreate,
    FilePolicyUpdate,
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
    # organization_id is NOT a DTO-generated flag — org targeting is handled by
    # the unified --org standard (org_option), so the generator must not emit
    # an --organization flag for it.
    assert "organization_id" not in flag_to_dest.values()


def _required_flag_dests(model_cls: type) -> set[str]:
    """Return the dest names of generated flags marked ``required=True``."""
    excludes = DTO_EXCLUDES.get(model_cls.__name__, set())
    refs = DTO_REF_LOOKUPS.get(model_cls.__name__, {})
    decorators = build_cli_flags(model_cls, exclude=excludes, verb_ref_lookups=refs)

    @decorators[0]  # type: ignore[misc]
    def _stub() -> None:  # pragma: no cover - introspection helper
        return None

    fn = _stub
    for dec in decorators[1:]:
        fn = dec(fn)
    return {
        p.name  # type: ignore[attr-defined]
        for p in fn.__click_params__  # type: ignore[attr-defined]
        if getattr(p, "required", False)
    }


def test_required_dto_fields_become_required_flags() -> None:
    """A DTO field with no default surfaces as a ``required`` CLI flag, so the
    command fails fast ('Missing option') instead of letting the server 422 —
    and ``cli-reference.md`` / ``--help`` show ``[required]``."""
    from src.models.contracts.forms import FormCreate
    from src.models.contracts.tables import TableCreate
    from src.models.contracts.agents import AgentCreate
    from src.models.contracts.config import ConfigCreate

    assert _required_flag_dests(FormCreate) == {"name", "form_schema"}
    assert _required_flag_dests(TableCreate) == {"name"}
    assert _required_flag_dests(AgentCreate) == {"name", "system_prompt"}
    assert _required_flag_dests(ConfigCreate) == {"key", "value"}


def test_update_dtos_force_no_required_flags() -> None:
    """Update DTOs make every field optional (partial update), so the generator
    must NOT mark any field flag required."""
    from src.models.contracts.forms import FormUpdate
    from src.models.contracts.tables import TableUpdate
    from src.models.contracts.agents import AgentUpdate

    assert _required_flag_dests(FormUpdate) == set()
    assert _required_flag_dests(TableUpdate) == set()
    assert _required_flag_dests(AgentUpdate) == set()


def test_required_flags_are_actually_enforced_by_click() -> None:
    """Required must be ENFORCED at parse time, not merely set as an attribute.

    Regression guard for a Click 8.4.1 trap: ``value_is_missing`` only treats a
    value as missing when it ``is UNSET`` — NOT when it is ``None``. So a flag
    built with both ``required=True`` AND ``default=None`` parses ``None`` as a
    *present* value, silently no-ops the required check, and the command runs
    through to a server 422 instead of failing fast. ``_required_flag_dests``
    (the attribute check) passed throughout that bug — only invoking the command
    with the flag omitted catches it. Build a throwaway command from FormCreate's
    flags, invoke it missing the required ``--form-schema``, and assert Click
    raises 'Missing option'."""
    import click
    from click.testing import CliRunner

    from src.models.contracts.forms import FormCreate

    flags = build_cli_flags(
        FormCreate,
        exclude=DTO_EXCLUDES.get("FormCreate", set()),
        verb_ref_lookups=DTO_REF_LOOKUPS.get("FormCreate", {}),
    )

    def _body(**_kwargs: object) -> None:  # noqa: ARG001 - body assertion only
        click.echo("body-ran")

    fn: object = _body
    for flag in reversed(flags):
        fn = flag(fn)
    probe = click.command()(fn)

    # Omit the required --form-schema (supply the other required --name).
    result = CliRunner().invoke(probe, ["--name", "x"])
    assert result.exit_code == 2, result.output
    assert "Missing option" in result.output
    assert "form-schema" in result.output
    assert "body-ran" not in result.output  # the body must NOT execute

    # A complete call parses cleanly (reaches the body, no Missing error).
    ok = CliRunner().invoke(probe, ["--name", "x", "--form-schema", "{}"])
    assert "Missing option" not in ok.output
    assert "body-ran" in ok.output
