"""Field-class metadata for Manifest* models + introspection.

A field's class declares its round-trip behavior (see the spec). Carried in Pydantic
Field(json_schema_extra=...). CONDITIONAL classes (e.g. Config.value is secret only when
config_type=='secret') use a STRING predicate key resolved through PREDICATES — never a
callable in json_schema_extra, which breaks pydantic schema generation."""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel


class FieldClass(str, Enum):
    IDENTITY = "identity"
    CONTENT = "content"
    ENVIRONMENT = "environment"
    SECRET = "secret"
    REFERENCE = "reference"


def _config_value_class(row: Any) -> FieldClass:
    ct = getattr(row, "config_type", None) if not isinstance(row, dict) else row.get("config_type")
    return FieldClass.SECRET if ct in ("secret",) else FieldClass.CONTENT


# String key -> resolver. The ONLY place callables live. Add new conditionals here.
PREDICATES: dict[str, Callable[[Any], FieldClass]] = {
    "config_value": _config_value_class,
}


def classify(
    field_class: FieldClass,
    *,
    match_key: bool = False,
    predicate: str | None = None,
    keep_on_portable: bool = False,
    import_owner: str = "direct",
) -> dict:
    extra: dict[str, Any] = {"bifrost_field_class": field_class.value}
    if match_key:
        extra["bifrost_match_key"] = True
    if keep_on_portable:
        extra["bifrost_keep_on_portable"] = True
    if predicate is not None:
        assert predicate in PREDICATES, f"unknown predicate key {predicate!r}"
        extra["bifrost_class_predicate"] = predicate  # a STRING, schema-safe
    assert import_owner in ("direct", "indexer", "restamp"), f"bad import_owner {import_owner!r}"
    if import_owner != "direct":
        extra["bifrost_import_owner"] = import_owner
    return {"json_schema_extra": extra}


def import_owner_of(model: type[BaseModel], field: str) -> str:
    return _extra(model, field).get("bifrost_import_owner", "direct")


def iter_manifest_models() -> list[type[BaseModel]]:
    import bifrost.manifest as _m  # lazy — avoid import cycle
    out = []
    for name in dir(_m):
        obj = getattr(_m, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and name.startswith("Manifest") and name != "Manifest":
            out.append(obj)
    return out


def _extra(model: type[BaseModel], field: str) -> dict:
    return model.model_fields[field].json_schema_extra or {}  # type: ignore[return-value]


def field_class_of(model: type[BaseModel], field: str, row: Any | None = None) -> FieldClass:
    extra = _extra(model, field)
    pred_key = extra.get("bifrost_class_predicate")
    if pred_key is not None and row is not None:
        return PREDICATES[pred_key](row)
    return FieldClass(extra["bifrost_field_class"])


def match_keys(model: type[BaseModel]) -> tuple[str, ...]:
    return tuple(f for f in model.model_fields if _extra(model, f).get("bifrost_match_key"))


def is_keep_on_portable(model: type[BaseModel], field: str) -> bool:
    return bool(_extra(model, field).get("bifrost_keep_on_portable"))
