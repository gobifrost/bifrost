"""
DTO-driven flag and body generators for CLI / MCP parity.

Walks ``XxxCreate`` / ``XxxUpdate`` Pydantic models and emits:

- :func:`build_cli_flags` — list of Click option decorators for a command.
- :func:`assemble_body` — REST request payload builder from parsed args.

MCP tools declare parameters directly on their ``@mcp.tool`` function via
regular Python kwargs; the parity test in ``tests/e2e/mcp/test_mcp_parity.py``
introspects those signatures against the DTO ``model_fields`` to catch drift.

Field handling rules (uniform across CLI and MCP):

* Bool → tri-state ``--flag/--no-flag`` (unset means don't send).
* ``list[str]`` → repeatable flag, or comma-split when name ends in ``_ids``.
* ``dict`` → ``@file`` loader (``--schema @schema.yaml``) or JSON string.
* Enum → ``click.Choice`` of enum values.
* Plain scalar → typed flag.
* Field declared in ``verb_ref_lookups`` → string ref flag, resolved via
  :class:`bifrost.refs.RefResolver` in :func:`assemble_body`.

Per-entity exclude registries are declared at module level and consumed by
the field-parity tests in ``tests/unit/test_dto_flags.py`` so adding an
unhandled DTO field fails loudly.
"""

from __future__ import annotations

import enum
import json
from pathlib import Path
from types import UnionType
from typing import Any, Callable, Union, get_args, get_origin
from uuid import UUID

import click
import yaml

from bifrost.refs import RefKind, RefResolver

# ---------------------------------------------------------------------------
# Per-entity exclude registries (UI-managed or out-of-scope fields).
#
# Tests in ``tests/unit/test_dto_flags.py`` enforce that the union of
# generated flag names and the per-DTO exclude set equals the set of writable
# DTO fields. New DTO fields therefore fail the test until they are either
# exposed as flags or explicitly excluded with a documented reason.
# ---------------------------------------------------------------------------

#: Per-DTO field exclusions, keyed by ``model_cls.__name__``.
DTO_EXCLUDES: dict[str, set[str]] = {
    # Organizations: ``domain`` is auto-provisioning policy; ``settings`` is a
    # UI-managed JSON blob; ``is_provider`` is immutable post-create.
    "OrganizationCreate": {"domain", "settings", "is_provider"},
    "OrganizationUpdate": {"domain", "settings"},
    # Workflows: code-defined or UI-managed surface metadata.
    "WorkflowUpdateRequest": {
        "display_name",
        "tool_description",
        "time_saved",
        "value",
        "cache_ttl_seconds",
        "allowed_methods",
        "execution_mode",
        "disable_global_key",
    },
    # Integrations: ``oauth_provider`` (out-of-scope) — declared even when
    # absent so adding the field later flags the new surface.
    "IntegrationCreate": {"oauth_provider"},
    "IntegrationUpdate": {"oauth_provider"},
    # Integration mappings: ``oauth_token_id`` is set by the OAuth flow in UI.
    "IntegrationMappingCreate": {"oauth_token_id"},
    "IntegrationMappingUpdate": {"oauth_token_id"},
    # Applications: ``icon`` is UI-managed.
    # Note: ``repo_path`` is intentionally absent from ApplicationCreate /
    # ApplicationUpdate — it's mutated via ``bifrost apps replace`` (a narrow
    # surface with validation), not through the generic create/update flow.
    "ApplicationCreate": {"icon"},
    "ApplicationUpdate": {"icon"},
    # Event sources: the nested ``webhook`` / ``schedule`` objects are
    # surfaced by ``bifrost events create-source`` / ``update-source`` as
    # flat flags (``--cron`` / ``--timezone`` / ``--schedule-enabled``
    # collapse into ``schedule``; ``--adapter`` / ``--webhook-integration`` /
    # ``--webhook-config`` collapse into ``webhook``). Excluded here so the
    # DTO-driven generator doesn't produce opaque ``--webhook`` / ``--schedule``
    # JSON-object flags alongside the flat ones.
    "EventSourceCreate": {"webhook", "schedule"},
    "EventSourceUpdate": {"webhook", "schedule"},
}

#: Per-DTO field renames applied to the assembled body.
#: ``{src_name: target_name}`` — ``src_name`` is the DTO field, ``target_name``
#: is the REST payload key.
DTO_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "ConfigCreate": {"config_type": "type"},
    "ConfigUpdate": {"config_type": "type"},
}

#: Per-DTO ref-lookup map — ``{field_name: ref_kind}``.
#: Fields listed here become flags named after the kind (without ``_id``)
#: and are resolved to a UUID by :class:`RefResolver` before assembly.
DTO_REF_LOOKUPS: dict[str, dict[str, str]] = {
    "FormCreate": {
        "workflow_id": "workflow",
        "launch_workflow_id": "workflow",
        "organization_id": "org",
    },
    "FormUpdate": {
        "workflow_id": "workflow",
        "launch_workflow_id": "workflow",
        "organization_id": "org",
    },
    "AgentCreate": {"organization_id": "org"},
    "AgentUpdate": {"organization_id": "org"},
    "ApplicationCreate": {"organization_id": "org"},
    "ApplicationUpdate": {},  # ``scope`` is free-form, not a ref
    "ConfigCreate": {"organization_id": "org"},
    "TableCreate": {"organization_id": "org"},
    "TableUpdate": {"application_id": "app"},
    "IntegrationUpdate": {"list_entities_data_provider_id": "workflow"},
    "IntegrationMappingCreate": {"organization_id": "org"},
    "EventSourceCreate": {"organization_id": "org"},
    "EventSourceUpdate": {"organization_id": "org"},
    "EventSubscriptionCreate": {"workflow_id": "workflow", "agent_id": "agent"},
}


# ---------------------------------------------------------------------------
# Type introspection helpers
# ---------------------------------------------------------------------------


def _unwrap_optional(tp: Any) -> Any:
    """Strip ``Optional[T]`` / ``T | None`` and return ``T``.

    Returns ``tp`` unchanged when not optional.
    """
    origin = get_origin(tp)
    if origin is Union or origin is UnionType:
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        # Multi-arm unions (e.g. ``dict | FormSchema``) collapse to the first
        # non-None arm — generators only need a coarse classifier.
        return args[0] if args else tp
    return tp


def _is_enum_type(tp: Any) -> bool:
    inner = _unwrap_optional(tp)
    return isinstance(inner, type) and issubclass(inner, enum.Enum)


def _enum_choices(tp: Any) -> list[str]:
    inner = _unwrap_optional(tp)
    return [str(member.value) for member in inner]  # type: ignore[union-attr]


def _is_list_str(tp: Any) -> bool:
    inner = _unwrap_optional(tp)
    return get_origin(inner) in (list, list)  # ``list[T]`` or ``List[T]``


def _is_dict(tp: Any) -> bool:
    inner = _unwrap_optional(tp)
    origin = get_origin(inner)
    if origin in (dict,):
        return True
    # Direct ``dict`` or non-parametrised ``dict``.
    return inner is dict


def _is_bool(tp: Any) -> bool:
    return _unwrap_optional(tp) is bool


def _is_int(tp: Any) -> bool:
    return _unwrap_optional(tp) is int


def _is_float(tp: Any) -> bool:
    return _unwrap_optional(tp) is float


def _is_uuid(tp: Any) -> bool:
    return _unwrap_optional(tp) is UUID


def _kebab(name: str) -> str:
    return name.replace("_", "-")


def _ref_flag_name(field_name: str, ref_kind: str) -> str:
    """Derive a flag name for a ref-lookup field.

    ``workflow_id → --workflow`` (strip ``_id``); when the field stem already
    matches the ref kind nothing changes.
    """
    stem = field_name[:-3] if field_name.endswith("_id") else field_name
    if stem == ref_kind:
        return _kebab(stem)
    # Disambiguate paired refs (e.g. ``launch_workflow_id``) by keeping the
    # field stem rather than the bare kind.
    return _kebab(stem)


# ---------------------------------------------------------------------------
# @file loader (dict fields)
# ---------------------------------------------------------------------------


def load_dict_value(raw: str | None) -> dict[str, Any] | None:
    """Resolve a CLI ``--schema`` argument to a dict.

    ``@path`` loads YAML or JSON from disk; otherwise the value is parsed as
    a JSON literal. Returns ``None`` when ``raw`` is ``None``.
    """
    if raw is None:
        return None
    if raw.startswith("@"):
        path = Path(raw[1:])
        text = path.read_text(encoding="utf-8")
        # YAML is a JSON superset — load YAML for both ``.yaml`` and ``.json``.
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise click.BadParameter(
                f"file {path} must contain a JSON/YAML object, got "
                f"{type(loaded).__name__}"
            )
        return loaded
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise click.BadParameter(
            f"value must be a JSON object, got {type(parsed).__name__}"
        )
    return parsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _generated_flag_fields(
    model_cls: type,
    *,
    exclude: set[str],
) -> list[str]:
    """Return the DTO field names that ``build_cli_flags`` would expose.

    Used by the field-parity tests; mirrors the iteration in
    :func:`build_cli_flags` without constructing Click decorators.
    """
    return [
        name for name in model_cls.model_fields if name not in exclude
    ]


def build_cli_flags(
    model_cls: type,
    *,
    exclude: set[str],
    verb_ref_lookups: dict[str, str],
) -> list[Callable[[Callable[..., Any]], Callable[..., Any]]]:
    """Build Click option decorators for a DTO.

    Args:
        model_cls: Pydantic v2 model whose ``model_fields`` are inspected.
        exclude: Field names to skip (UI-managed surface).
        verb_ref_lookups: ``{field_name: ref_kind}`` for fields that accept
            a name/UUID ref. The flag name becomes the ref kind (``_id`` is
            stripped from the field) and a string is collected; resolution
            happens in :func:`assemble_body`.

    Returns:
        A list of Click option decorators ready to be applied to a command
        function (most-specific flag last so it applies first).
    """
    decorators: list[Callable[[Callable[..., Any]], Callable[..., Any]]] = []
    for name, field in model_cls.model_fields.items():
        if name in exclude:
            continue
        annotation = field.annotation

        if name in verb_ref_lookups:
            ref_kind = verb_ref_lookups[name]
            flag = f"--{_ref_flag_name(name, ref_kind)}"
            decorators.append(
                click.option(
                    flag,
                    name,
                    type=str,
                    default=None,
                    help=f"{ref_kind} ref (UUID or name) for {name}.",
                )
            )
            continue

        if _is_bool(annotation):
            kebab = _kebab(name)
            decorators.append(
                click.option(
                    f"--{kebab}/--no-{kebab}",
                    name,
                    default=None,
                    help=f"{name} (tri-state; omit to leave unchanged).",
                )
            )
            continue

        if _is_list_str(annotation):
            inner_args = get_args(_unwrap_optional(annotation))
            inner_type = inner_args[0] if inner_args else str
            multiple = True
            # ``foo_ids`` accepts comma-separated values.
            comma_split = name.endswith("_ids")
            decorators.append(
                click.option(
                    f"--{_kebab(name)}",
                    name,
                    type=str if inner_type is not int else int,
                    multiple=multiple,
                    help=(
                        f"{name} (repeat for multiple"
                        f"{'; comma-split also accepted' if comma_split else ''})."
                    ),
                )
            )
            continue

        if _is_dict(annotation):
            decorators.append(
                click.option(
                    f"--{_kebab(name)}",
                    name,
                    type=str,
                    default=None,
                    help=(
                        f"{name} as JSON literal or @path to a YAML/JSON file."
                    ),
                )
            )
            continue

        if _is_enum_type(annotation):
            decorators.append(
                click.option(
                    f"--{_kebab(name)}",
                    name,
                    type=click.Choice(_enum_choices(annotation)),
                    default=None,
                    help=name,
                )
            )
            continue

        if _is_int(annotation):
            decorators.append(
                click.option(
                    f"--{_kebab(name)}",
                    name,
                    type=int,
                    default=None,
                    help=name,
                )
            )
            continue

        if _is_float(annotation):
            decorators.append(
                click.option(
                    f"--{_kebab(name)}",
                    name,
                    type=float,
                    default=None,
                    help=name,
                )
            )
            continue

        if _is_uuid(annotation):
            decorators.append(
                click.option(
                    f"--{_kebab(name)}",
                    name,
                    type=str,
                    default=None,
                    help=f"{name} (UUID).",
                )
            )
            continue

        # Fallback: plain string scalar.
        decorators.append(
            click.option(
                f"--{_kebab(name)}",
                name,
                type=str,
                default=None,
                help=name,
            )
        )
    return decorators


async def assemble_body(
    model_cls: type,
    parsed_args: dict[str, Any],
    *,
    resolver: RefResolver,
    field_aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the REST request payload from parsed CLI / MCP arguments.

    - Drops keys whose value is ``None`` or an empty tuple (unset flags).
    - Resolves ref-lookup fields via ``resolver`` (UUIDs pass straight
      through; names/slugs/``path::func`` are looked up).
    - Loads ``dict`` fields via :func:`load_dict_value` when a string was
      collected from CLI input.
    - Splits comma-separated values for ``*_ids`` repeatable flags.
    - Applies ``field_aliases`` (and any registry entry for ``model_cls``)
      to rename payload keys.
    """
    aliases = dict(DTO_FIELD_ALIASES.get(model_cls.__name__, {}))
    if field_aliases:
        aliases.update(field_aliases)
    ref_lookups = DTO_REF_LOOKUPS.get(model_cls.__name__, {})
    body: dict[str, Any] = {}

    for name, field in model_cls.model_fields.items():
        if name not in parsed_args:
            continue
        value = parsed_args[name]
        if value is None:
            continue
        # Click ``multiple=True`` collects an empty tuple when nothing was
        # passed — treat that as unset.
        if isinstance(value, tuple) and not value:
            continue

        annotation = field.annotation

        if name in ref_lookups:
            body[name] = await resolver.resolve(
                ref_lookups[name],  # type: ignore[arg-type]
                str(value),
            )
            continue

        if _is_list_str(annotation):
            collected: list[Any] = []
            iterable = list(value) if isinstance(value, (list, tuple)) else [value]
            for item in iterable:
                if isinstance(item, str) and name.endswith("_ids") and "," in item:
                    collected.extend(p.strip() for p in item.split(",") if p.strip())
                else:
                    collected.append(item)
            # Resolve role refs when the field is ``role_ids`` and entries
            # look like names rather than UUIDs.
            if name == "role_ids":
                resolved: list[str] = []
                for item in collected:
                    resolved.append(await resolver.resolve("role", str(item)))
                collected = resolved
            body[name] = collected
            continue

        if _is_dict(annotation) and isinstance(value, str):
            body[name] = load_dict_value(value)
            continue

        if _is_uuid(annotation) and isinstance(value, str):
            body[name] = value
            continue

        body[name] = value

    for src, target in aliases.items():
        if src in body:
            body[target] = body.pop(src)

    return body


__all__ = [
    "DTO_EXCLUDES",
    "DTO_FIELD_ALIASES",
    "DTO_REF_LOOKUPS",
    "RefKind",
    "assemble_body",
    "build_cli_flags",
    "load_dict_value",
]
