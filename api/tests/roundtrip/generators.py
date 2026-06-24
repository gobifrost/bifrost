"""Deterministic fixture generators. No randomness, no Hypothesis."""
from __future__ import annotations

import types
import typing
import uuid
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

_NS = uuid.UUID("00000000-0000-0000-0000-0000000000ff")  # fixed namespace


def _det_uuid(model_name: str, field: str) -> str:
    return str(uuid.uuid5(_NS, f"{model_name}.{field}"))


def _is_union(ann: Any) -> bool:
    # BOTH PEP 604 (X | None -> types.UnionType) AND typing.Union[...]
    return get_origin(ann) in (typing.Union, types.UnionType)


def _unwrap_optional(ann: Any) -> Any:
    if _is_union(ann):
        args = [a for a in get_args(ann) if a is not type(None)]
        return args[0] if args else ann
    return ann


# Domain-valid sentinels for str fields that are enum-constrained downstream.
# Keyed by (model_name, field_name) first, then bare field_name as fallback.
# These survive the real import/deploy path (not just pydantic validation).
DOMAIN_VALUES: dict[tuple[str, str] | str, Any] = {
    "access_level": "role_based",
    ("ManifestAgent", "channels"): ["chat"],
    ("ManifestEventSource", "source_type"): "schedule",
    ("ManifestEventSource", "overlap_policy"): "skip",
    ("ManifestConfig", "config_type"): "string",
    ("ManifestSolutionConfigSchema", "type"): "string",
    ("ManifestIntegrationConfigSchema", "type"): "string",
    ("ManifestApp", "app_model"): "standalone_v2",
    # `list[dict[str, Any]]` — the generic list-of-dict generator would emit a
    # list of strings; supply a valid policy-document shape instead.
    ("ManifestFilePolicy", "policies"): [{"name": "SENT::policy", "actions": ["read"]}],
    # `validate_location_name` enforces ^[a-z0-9][a-z0-9-]*$; the generic
    # SENT:: sentinel has uppercase and "::" and is rejected.
    ("ManifestFiles", "locations"): ["finance"],
}


def sentinel_for(model_name: str, name: str, info: FieldInfo) -> Any:
    # Domain-value override — checked before type dispatch
    domain = DOMAIN_VALUES.get((model_name, name)) or DOMAIN_VALUES.get(name)
    if domain is not None:
        return domain

    if name == "id" or name.endswith("_id"):
        return _det_uuid(model_name, name)

    ann = _unwrap_optional(info.annotation)
    origin = get_origin(ann)

    if ann is bool:
        return True
    if ann is int:
        return 4242
    if ann is str:
        return f"SENT::{model_name}.{name}"
    if origin is Literal:
        return get_args(ann)[0]  # a VALID literal member
    if origin is list:
        args = get_args(ann)
        inner = _unwrap_optional(args[0]) if args else str
        if get_origin(inner) is Literal:
            return [get_args(inner)[0]]
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return [all_fields_populated(inner)]
        return [f"SENT::{model_name}.{name}.0"]
    if origin is dict or (ann is dict and origin is None):
        # bare `dict` has origin None after unwrapping Optional
        args = get_args(ann)
        if len(args) == 2:
            vt = _unwrap_optional(args[1])
            if isinstance(vt, type) and issubclass(vt, BaseModel):
                return {f"SENT_K::{name}": all_fields_populated(vt)}
            return {f"SENT_K::{name}": f"SENT_V::{name}"}
        # bare dict / dict[str, str] with no args
        return {f"SENT_K::{name}": f"SENT_V::{name}"}
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        # nested model (e.g. ClaimQuery inside ManifestCustomClaim)
        return all_fields_populated(ann)
    # object / Any / unknown
    return f"SENT::{model_name}.{name}"


def all_fields_populated(model: type[BaseModel]) -> dict:
    return {n: sentinel_for(model.__name__, n, i) for n, i in model.model_fields.items()}


def each_field_isolated(model: type[BaseModel]) -> list[dict]:
    base = {
        n: sentinel_for(model.__name__, n, i)
        for n, i in model.model_fields.items()
        if i.is_required()
    }
    out = []
    for n, i in model.model_fields.items():
        f = dict(base)
        f[n] = sentinel_for(model.__name__, n, i)
        out.append(f)
    return out
