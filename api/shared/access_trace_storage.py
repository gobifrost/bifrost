"""Compact, versioned storage for recorded access-check traces.

The audit API and re-judging path consume the expanded details shape.  New
rows store only data that cannot be reconstructed from the trace order, audit
outcome, and top-level enforcement flag; unversioned rows remain untouched.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


ACCESS_TRACE_STORAGE_VERSION = 1
_VERSION_KEY = "access_trace_storage_version"
_STEP_KEYS = frozenset({"key", "label", "status", "reason", "facts"})
_COMPACT_STEP_KEYS = _STEP_KEYS - {"label"}
_TRACE_KEYS = frozenset({"outcome", "enforced", "steps"})
_COMPACT_TRACE_KEYS = frozenset({"steps", "order"})
_REACHED_STATUSES = frozenset({"passed", "stopped", "not_applicable"})
_STORED_STATUSES = _REACHED_STATUSES | {"not_reached"}
_DETAIL_REQUIRED_KEYS = frozenset({"enforced", "direct", "workflow_id", "inputs", "trace"})
_DETAIL_OPTIONAL_KEYS = frozenset({"agent_run_id"})

# These labels come from the authorization decision core.  A changed or
# noncanonical label is kept in the compact order so expansion never guesses.
CANONICAL_TRACE_STEP_LABELS = {
    "entry": "Entry",
    "permission": "Permission",
    "policy": "Policy",
    "powers": "Workflow powers",
    "privileged_target": "Privileged User",
    "run_as_user": "Run As User",
    "run_user": "Run user",
    "target": "Target in reach",
    "workflow_access": "Workflow access",
}


def canonical_trace_step_label(key: str) -> str | None:
    """Return the decision core's label for a standard trace step."""
    return CANONICAL_TRACE_STEP_LABELS.get(key)


def compact_access_check_details(details: Mapping[str, Any]) -> dict[str, Any]:
    """Return the compact details written for a new ``access.check`` row.

    This accepts the writer's established details shape and rejects a shape
    that cannot be expanded losslessly.  The audit row retains the operation
    and outcome, so those duplicates are omitted from the stored JSON.
    """
    stored = deepcopy(dict(details))
    _require_bool(stored, "enforced")
    _require_bool(stored, "direct")
    trace = _raw_trace(stored.pop("trace", None))
    inputs = _mapping(stored.get("inputs"), "inputs")
    operation = inputs.pop("operation", None)
    if not isinstance(operation, str) or not operation:
        raise ValueError("access.check inputs.operation must be a nonempty string")

    stored["inputs"] = inputs
    stored.pop("today", None)
    stored.pop("proposed_attribution", None)
    stored[_VERSION_KEY] = ACCESS_TRACE_STORAGE_VERSION
    stored["trace"] = _compact_trace(trace, enforced=stored["enforced"])
    _validate_compact_detail_keys(stored)
    return stored


def expand_access_check_details(
    details: Mapping[str, Any], *, outcome: str, operation_id: str | None
) -> dict[str, Any]:
    """Expand compact details to the established access-check details shape.

    Unversioned rows are legacy rows and are returned unchanged in content.
    Versioned rows are validated strictly: returning a fabricated trace would
    make an audit explanation look like a decision that never happened.
    """
    expanded = deepcopy(dict(details))
    if _VERSION_KEY not in expanded:
        return expanded
    version = expanded.pop(_VERSION_KEY)
    if type(version) is not int or version != ACCESS_TRACE_STORAGE_VERSION:
        raise ValueError(f"unsupported access trace storage version: {version!r}")
    if outcome not in {"success", "failure"}:
        raise ValueError(f"invalid audit outcome for compact access trace: {outcome!r}")
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("compact access trace requires a nonempty operation_id")
    _validate_compact_detail_keys(expanded)

    enforced = _require_bool(expanded, "enforced")
    _require_bool(expanded, "direct")
    inputs = _mapping(expanded.get("inputs"), "inputs")
    if "operation" in inputs:
        raise ValueError("compact access trace inputs must omit operation")
    if "target" not in inputs:
        raise ValueError("compact access trace inputs must include target")
    inputs["operation"] = operation_id
    expanded["inputs"] = inputs
    expanded["trace"] = _expand_trace(expanded.get("trace"), outcome=outcome, enforced=enforced)
    return expanded


def _compact_trace(trace: dict[str, Any], *, enforced: bool) -> dict[str, Any]:
    if trace["enforced"] != enforced:
        raise ValueError("access trace enforcement must match access.check details")
    order: list[str | dict[str, str]] = []
    steps: list[dict[str, Any]] = []
    for step in trace["steps"]:
        key, label, status, reason, facts = _step(step)
        canonical_label = canonical_trace_step_label(key)
        order.append(key if canonical_label == label else {"key": key, "label": label})
        if status == "not_reached" and not reason and not facts:
            continue
        steps.append({"key": key, "status": status, "reason": reason, "facts": facts})
    return {"steps": steps, "order": order}


def _expand_trace(value: Any, *, outcome: str, enforced: bool) -> dict[str, Any]:
    compact = _mapping(value, "compact trace")
    if set(compact) != _COMPACT_TRACE_KEYS:
        raise ValueError("compact access trace must contain only steps and order")
    raw_steps = compact["steps"]
    raw_order = compact["order"]
    if not isinstance(raw_steps, list) or not isinstance(raw_order, list):
        raise ValueError("compact access trace steps and order must be lists")

    reached: dict[str, dict[str, Any]] = {}
    for raw_step in raw_steps:
        step = _mapping(raw_step, "compact access trace step")
        if set(step) != _COMPACT_STEP_KEYS:
            raise ValueError("compact access trace steps must omit only label")
        key = step["key"]
        status = step["status"]
        reason = step["reason"]
        facts = _mapping(step["facts"], "compact access trace step facts")
        if not isinstance(key, str) or not key:
            raise ValueError("compact access trace step key must be a nonempty string")
        if status not in _STORED_STATUSES:
            raise ValueError("invalid compact access trace step status")
        if not isinstance(reason, str):
            raise ValueError("compact access trace step reason must be a string")
        if key in reached:
            raise ValueError(f"duplicate compact access trace step key: {key}")
        reached[key] = {"key": key, "status": status, "reason": reason, "facts": facts}

    steps: list[dict[str, Any]] = []
    ordered_keys: set[str] = set()
    for raw_item in raw_order:
        key, label = _order_item(raw_item)
        if key in ordered_keys:
            raise ValueError(f"duplicate compact access trace order key: {key}")
        ordered_keys.add(key)
        stored_step = reached.pop(key, None)
        if stored_step is None:
            steps.append({"key": key, "label": label, "status": "not_reached", "reason": "", "facts": {}})
        else:
            steps.append({**stored_step, "label": label})
    if reached:
        raise ValueError("compact access trace has reached steps absent from order")
    return {"outcome": outcome, "enforced": enforced, "steps": steps}


def _raw_trace(value: Any) -> dict[str, Any]:
    trace = _mapping(value, "access trace")
    if set(trace) != _TRACE_KEYS:
        raise ValueError("access trace must contain outcome, enforced, and steps")
    if trace["outcome"] not in {"success", "failure"}:
        raise ValueError("access trace outcome must be success or failure")
    if type(trace["enforced"]) is not bool:
        raise ValueError("access trace enforced must be a boolean")
    if not isinstance(trace["steps"], list):
        raise ValueError("access trace steps must be a list")
    steps = []
    keys: set[str] = set()
    for raw_step in trace["steps"]:
        key, label, status, reason, facts = _step(raw_step)
        if key in keys:
            raise ValueError(f"duplicate access trace step key: {key}")
        keys.add(key)
        steps.append({"key": key, "label": label, "status": status, "reason": reason, "facts": facts})
    return {"outcome": trace["outcome"], "enforced": trace["enforced"], "steps": steps}


def _step(value: Any) -> tuple[str, str, str, str, dict[str, Any]]:
    step = _mapping(value, "access trace step")
    if set(step) != _STEP_KEYS:
        raise ValueError("access trace steps must contain key, label, status, reason, and facts")
    key, label, status, reason = step["key"], step["label"], step["status"], step["reason"]
    facts = _mapping(step["facts"], "access trace step facts")
    if not isinstance(key, str) or not key:
        raise ValueError("access trace step key must be a nonempty string")
    if not isinstance(label, str):
        raise ValueError("access trace step label must be a string")
    if status not in {*_REACHED_STATUSES, "not_reached"}:
        raise ValueError("invalid access trace step status")
    if not isinstance(reason, str):
        raise ValueError("access trace step reason must be a string")
    return key, label, status, reason, facts


def _order_item(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        label = canonical_trace_step_label(value)
        if label is None:
            raise ValueError(f"unknown canonical access trace step key: {value}")
        return value, label
    item = _mapping(value, "compact access trace order item")
    if set(item) != {"key", "label"}:
        raise ValueError("noncanonical compact access trace order items need key and label")
    key, label = item["key"], item["label"]
    if not isinstance(key, str) or not key or not isinstance(label, str):
        raise ValueError("compact access trace order key and label must be strings")
    if canonical_trace_step_label(key) == label:
        raise ValueError("canonical compact access trace labels must use the key form")
    return key, label


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _validate_compact_detail_keys(details: Mapping[str, Any]) -> None:
    keys = set(details)
    keys.discard(_VERSION_KEY)
    if not _DETAIL_REQUIRED_KEYS <= keys or not keys <= _DETAIL_REQUIRED_KEYS | _DETAIL_OPTIONAL_KEYS:
        raise ValueError("invalid compact access.check details shape")
    workflow_id = details["workflow_id"]
    if workflow_id is not None and not isinstance(workflow_id, str):
        raise ValueError("compact access trace workflow_id must be a string or null")
    agent_run_id = details.get("agent_run_id")
    if agent_run_id is not None and not isinstance(agent_run_id, str):
        raise ValueError("compact access trace agent_run_id must be a string")


def _require_bool(details: Mapping[str, Any], key: str) -> bool:
    value = details.get(key)
    if type(value) is not bool:
        raise ValueError(f"access.check details.{key} must be a boolean")
    return value
