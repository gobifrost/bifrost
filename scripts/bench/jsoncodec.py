"""Strict, lossless JSON encoding for benchmark result artifacts.

JSON has no spelling for NaN or either infinity.  The harness records those
values explicitly so result artifacts remain portable strict JSON while their
in-memory meaning can be restored when read back.
"""

from __future__ import annotations

import copy
import math
from typing import Any


SCHEMA_VERSION = 3
NON_FINITE_NUMBERS = "non_finite_numbers"
_RESERVED_TOP_LEVEL = frozenset({NON_FINITE_NUMBERS})
_KINDS = frozenset({"positive_infinity", "negative_infinity", "nan"})
_LEGACY_SCHEMA_VERSIONS = frozenset({1, 2})


def encode(result: dict[str, Any]) -> dict[str, Any]:
    """Return a strict-JSON v3 copy of a benchmark result.

    Non-finite floats are represented by ``null`` at their original location
    and by deterministic top-level metadata.  The input is never changed.
    """
    _require_result_dict(result)
    if NON_FINITE_NUMBERS in result:
        raise ValueError(f"top-level key {NON_FINITE_NUMBERS!r} is reserved")
    if (
        "schema_version" in result
        and type(result["schema_version"]) is float
        and not math.isfinite(result["schema_version"])
    ):
        raise ValueError("schema_version must be finite")

    non_finite: list[dict[str, Any]] = []
    encoded = _encode_value(result, [], non_finite)
    assert isinstance(encoded, dict)  # established by _require_result_dict
    encoded["schema_version"] = SCHEMA_VERSION
    encoded[NON_FINITE_NUMBERS] = non_finite
    return encoded


def decode(encoded: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of an encoded result, restoring v3 non-finite floats.

    Older artifacts are copied unchanged. A v3 artifact is fully validated
    before any restored value is written into the returned copy; its sidecar
    metadata is then removed so that copy can be encoded again.
    """
    _require_result_dict(encoded)
    if "schema_version" not in encoded:
        if NON_FINITE_NUMBERS in encoded:
            raise ValueError("non_finite_numbers requires schema_version 3")
        return copy.deepcopy(encoded)

    schema_version = encoded["schema_version"]
    if type(schema_version) is not int:
        raise ValueError("schema_version must be an integer")
    if schema_version in _LEGACY_SCHEMA_VERSIONS:
        if NON_FINITE_NUMBERS in encoded:
            raise ValueError("non_finite_numbers requires schema_version 3")
        return copy.deepcopy(encoded)
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {schema_version!r}")

    if NON_FINITE_NUMBERS not in encoded:
        raise ValueError("v3 artifact is missing non_finite_numbers metadata")
    _validate_v3_tree(encoded)
    entries = _validate_metadata(encoded[NON_FINITE_NUMBERS], encoded)

    decoded = copy.deepcopy(encoded)
    del decoded[NON_FINITE_NUMBERS]
    for path, kind in entries:
        _replace_path(decoded, path, _float_for_kind(kind))
    return decoded


def _require_result_dict(value: Any) -> None:
    if type(value) is not dict:
        raise ValueError("benchmark result must be a plain dict")


def _encode_value(value: Any, path: list[str | int], non_finite: list[dict[str, Any]]) -> Any:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if math.isfinite(value):
            return value
        non_finite.append({"path": list(path), "kind": _kind_for_float(value)})
        return None
    if type(value) in (list, tuple):
        return [_encode_value(item, [*path, index], non_finite) for index, item in enumerate(value)]
    if type(value) is dict:
        _validate_dict_keys(value, path)
        return {
            key: _encode_value(value[key], [*path, key], non_finite)
            for key in sorted(value)
        }
    raise ValueError(f"unsupported JSON value at {_format_path(path)}: {type(value).__name__}")


def _validate_json_value(value: Any, path: list[str | int]) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"non-finite float without v3 metadata at {_format_path(path)}")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_json_value(item, [*path, index])
        return
    if type(value) is dict:
        _validate_dict_keys(value, path)
        for key in sorted(value):
            _validate_json_value(value[key], [*path, key])
        return
    raise ValueError(f"unsupported JSON value at {_format_path(path)}: {type(value).__name__}")


def _validate_dict_keys(value: dict[Any, Any], path: list[str | int]) -> None:
    for key in value:
        if type(key) is not str:
            raise ValueError(f"unsupported object key at {_format_path(path)}: {key!r}")


def _kind_for_float(value: float) -> str:
    if math.isnan(value):
        return "nan"
    return "positive_infinity" if value > 0 else "negative_infinity"


def _float_for_kind(kind: str) -> float:
    if kind == "positive_infinity":
        return float("inf")
    if kind == "negative_infinity":
        return float("-inf")
    if kind == "nan":
        return float("nan")
    raise AssertionError(f"validated kind unexpectedly unsupported: {kind!r}")


def _validate_v3_tree(encoded: dict[str, Any]) -> None:
    for key in encoded:
        if type(key) is not str:
            raise ValueError(f"unsupported object key at $: {key!r}")
    for key in sorted(encoded):
        if key == NON_FINITE_NUMBERS:
            continue
        _validate_json_value(encoded[key], [key])


def _validate_metadata(metadata: Any, encoded: dict[str, Any]) -> list[tuple[list[str | int], str]]:
    if type(metadata) is not list:
        raise ValueError("non_finite_numbers must be a list")

    entries: list[tuple[list[str | int], str]] = []
    seen: set[tuple[str | int, ...]] = set()
    for position, entry in enumerate(metadata):
        if type(entry) is not dict or set(entry) != {"path", "kind"}:
            raise ValueError(f"non_finite_numbers[{position}] must contain exactly path and kind")
        path = entry["path"]
        kind = entry["kind"]
        if type(path) is not list or not path:
            raise ValueError(f"non_finite_numbers[{position}].path must be a non-empty list")
        if type(kind) is not str or kind not in _KINDS:
            raise ValueError(f"non_finite_numbers[{position}].kind is invalid")
        normalized_path: list[str | int] = []
        for segment in path:
            if type(segment) is str:
                normalized_path.append(segment)
            elif type(segment) is int:  # bool is intentionally excluded.
                normalized_path.append(segment)
            else:
                raise ValueError(f"non_finite_numbers[{position}].path has an invalid segment")
        if normalized_path[0] in _RESERVED_TOP_LEVEL or normalized_path[0] == "schema_version":
            raise ValueError(f"non_finite_numbers[{position}].path targets codec metadata")
        path_key = tuple(normalized_path)
        if path_key in seen:
            raise ValueError(f"duplicate non-finite path at {_format_path(normalized_path)}")
        seen.add(path_key)
        target = _value_at_path(encoded, normalized_path)
        if target is not None:
            raise ValueError(f"non-finite path must target null at {_format_path(normalized_path)}")
        entries.append((normalized_path, kind))
    return entries


def _value_at_path(value: dict[str, Any], path: list[str | int]) -> Any:
    current: Any = value
    for segment in path:
        if type(segment) is str:
            if type(current) is not dict or segment not in current:
                raise ValueError(f"non-finite path does not exist at {_format_path(path)}")
            current = current[segment]
        else:
            if type(current) is not list or segment < 0 or segment >= len(current):
                raise ValueError(f"non-finite path does not exist at {_format_path(path)}")
            current = current[segment]
    return current


def _replace_path(value: dict[str, Any], path: list[str | int], replacement: float) -> None:
    current: Any = value
    for segment in path[:-1]:
        current = current[segment]
    final = path[-1]
    current[final] = replacement


def _format_path(path: list[str | int]) -> str:
    return "$" + "".join(f"[{segment!r}]" for segment in path)
