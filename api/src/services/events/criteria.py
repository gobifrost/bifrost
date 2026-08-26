"""Safe, bounded evaluation for event-subscription criteria."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import ValidationError

from src.models.contracts.events import (
    EventCriteria,
    EventCriteriaCondition,
    EventCriteriaNode,
    EventCriteriaOperator,
)
from src.models.orm.events import Event

CriteriaOutcome = Literal["matched", "not_matched", "evaluation_error"]


@dataclass(frozen=True)
class _NodeResult:
    matched: bool = False
    error_code: str | None = None


def evaluate_subscription_criteria(
    criteria_value: dict[str, Any] | EventCriteria | None,
    event: Event,
) -> dict[str, Any]:
    """Return safe decision evidence for one subscription and event.

    The returned object intentionally contains no event values or rendered
    expressions. It is safe to persist on ``EventDelivery`` and expose to an
    authorized operator.
    """
    if criteria_value is None:
        return {
            "criteria_version": None,
            "outcome": "matched",
            "code": "unconditional",
        }

    try:
        criteria = (
            criteria_value
            if isinstance(criteria_value, EventCriteria)
            else EventCriteria.model_validate(criteria_value)
        )
    except ValidationError:
        return {
            "criteria_version": _safe_version(criteria_value),
            "outcome": "evaluation_error",
            "code": "invalid_persisted_criteria",
        }

    envelope = build_event_criteria_envelope(event)
    try:
        result = _evaluate_node(criteria.root, envelope)
    except Exception:
        return {
            "criteria_version": criteria.version,
            "outcome": "evaluation_error",
            "code": "internal_evaluator_error",
        }

    if result.error_code:
        outcome: CriteriaOutcome = "evaluation_error"
        code = result.error_code
    elif result.matched:
        outcome = "matched"
        code = "criteria_matched"
    else:
        outcome = "not_matched"
        code = "criteria_not_matched"
    return {
        "criteria_version": criteria.version,
        "outcome": outcome,
        "code": code,
    }


def build_event_criteria_envelope(event: Event) -> dict[str, Any]:
    """Build the one normalized envelope used by every event ingress path."""
    data = event.data if isinstance(event.data, dict) else {}
    schedule = {
        "scheduled_time": data.get("scheduled_time"),
        "cron_expression": data.get("cron_expression"),
        "timezone": data.get("timezone"),
    }
    return {
        "event": {
            "type": event.event_type,
            "body": event.data,
            "headers": event.headers or {},
            "received_at": _iso(event.received_at),
            "source_ip": event.source_ip,
        },
        "schedule": schedule,
    }


def _evaluate_node(node: EventCriteriaNode, envelope: dict[str, Any]) -> _NodeResult:
    if isinstance(node, EventCriteriaCondition):
        return _evaluate_condition(node, envelope)

    if node.kind == "not":
        result = _evaluate_node(node.items[0], envelope)
        return result if result.error_code else _NodeResult(matched=not result.matched)

    if node.kind == "all":
        for item in node.items:
            result = _evaluate_node(item, envelope)
            if result.error_code or not result.matched:
                return result
        return _NodeResult(matched=True)

    for item in node.items:
        result = _evaluate_node(item, envelope)
        if result.error_code:
            return result
        if result.matched:
            return _NodeResult(matched=True)
    return _NodeResult(matched=False)


def _evaluate_condition(
    condition: EventCriteriaCondition,
    envelope: dict[str, Any],
) -> _NodeResult:
    found, actual = _resolve_field(envelope, condition.field)
    operator = condition.operator

    if operator == EventCriteriaOperator.EXISTS:
        return _NodeResult(matched=found)
    if operator == EventCriteriaOperator.NOT_EXISTS:
        return _NodeResult(matched=not found)
    if not found:
        return _NodeResult(matched=False)

    expected = condition.value
    if operator == EventCriteriaOperator.EQUALS:
        return _NodeResult(matched=_equal(actual, expected))
    if operator == EventCriteriaOperator.NOT_EQUALS:
        return _NodeResult(matched=not _equal(actual, expected))
    if operator == EventCriteriaOperator.IN:
        return _NodeResult(matched=any(_equal(actual, item) for item in expected))
    if operator == EventCriteriaOperator.NOT_IN:
        return _NodeResult(matched=all(not _equal(actual, item) for item in expected))

    if operator in {
        EventCriteriaOperator.CONTAINS,
        EventCriteriaOperator.STARTS_WITH,
        EventCriteriaOperator.ENDS_WITH,
    }:
        if not isinstance(actual, str):
            return _NodeResult(error_code="field_type_mismatch")
        if operator == EventCriteriaOperator.CONTAINS:
            return _NodeResult(matched=expected in actual)
        if operator == EventCriteriaOperator.STARTS_WITH:
            return _NodeResult(matched=actual.startswith(expected))
        return _NodeResult(matched=actual.endswith(expected))

    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return _NodeResult(error_code="field_type_mismatch")
    if operator == EventCriteriaOperator.GREATER_THAN:
        return _NodeResult(matched=actual > expected)
    if operator == EventCriteriaOperator.GREATER_THAN_OR_EQUAL:
        return _NodeResult(matched=actual >= expected)
    if operator == EventCriteriaOperator.LESS_THAN:
        return _NodeResult(matched=actual < expected)
    return _NodeResult(matched=actual <= expected)


def _resolve_field(envelope: dict[str, Any], field: str) -> tuple[bool, Any]:
    value: Any = envelope
    for segment in field.split("."):
        if not isinstance(value, dict) or segment not in value:
            return False, None
        value = value[segment]
    return True, value


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _safe_version(criteria_value: object) -> int | None:
    if not isinstance(criteria_value, dict):
        return None
    version = criteria_value.get("version")
    return version if isinstance(version, int) and not isinstance(version, bool) else None
