"""Structured event-subscription criteria contract and evaluator tests."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.models.contracts.events import EventCriteria
from src.services.events.criteria import evaluate_subscription_criteria


def _event(**body):
    return SimpleNamespace(
        event_type="ticket.created",
        data=body,
        headers={"X-Request-Id": "request-1"},
        received_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        source_ip="192.0.2.10",
    )


def _criteria(root: dict) -> EventCriteria:
    return EventCriteria.model_validate({"version": 1, "root": root})


def _condition(field: str, operator: str, value=...):
    result = {"kind": "condition", "field": field, "operator": operator}
    if value is not ...:
        result["value"] = value
    return result


def test_unconditional_subscription_matches_without_payload_evidence():
    decision = evaluate_subscription_criteria(None, _event(secret="do-not-copy"))

    assert decision == {
        "criteria_version": None,
        "outcome": "matched",
        "code": "unconditional",
    }
    assert "secret" not in repr(decision)


def test_nested_all_any_and_not_match_normalized_event_envelope():
    criteria = _criteria(
        {
            "kind": "all",
            "items": [
                _condition("event.type", "equals", "ticket.created"),
                {
                    "kind": "any",
                    "items": [
                        _condition("event.body.priority", "in", ["high", "urgent"]),
                        {
                            "kind": "not",
                            "items": [
                                _condition("event.body.owner", "exists"),
                            ],
                        },
                    ],
                },
            ],
        }
    )

    decision = evaluate_subscription_criteria(criteria, _event(priority="high"))

    assert decision == {
        "criteria_version": 1,
        "outcome": "matched",
        "code": "criteria_matched",
    }


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "outcome"),
    [
        ("not_equals", "low", "high", "matched"),
        ("not_in", "low", ["high", "urgent"], "matched"),
        ("contains", "incident: api", "api", "matched"),
        ("starts_with", "INC-123", "INC-", "matched"),
        ("ends_with", "report.csv", ".csv", "matched"),
        ("greater_than", 11, 10, "matched"),
        ("greater_than_or_equal", 10, 10, "matched"),
        ("less_than", 9, 10, "matched"),
        ("less_than_or_equal", 10, 10, "matched"),
        ("equals", True, 1, "not_matched"),
    ],
)
def test_operator_semantics(operator, actual, expected, outcome):
    criteria = _criteria(_condition("event.body.value", operator, expected))

    decision = evaluate_subscription_criteria(criteria, _event(value=actual))

    assert decision["outcome"] == outcome


def test_missing_field_is_non_match_but_not_exists_matches():
    missing = _criteria(_condition("event.body.missing", "equals", "value"))
    not_exists = _criteria(_condition("event.body.missing", "not_exists"))

    assert evaluate_subscription_criteria(missing, _event())["outcome"] == "not_matched"
    assert evaluate_subscription_criteria(not_exists, _event())["outcome"] == "matched"


def test_runtime_type_mismatch_fails_closed_with_safe_code():
    criteria = _criteria(_condition("event.body.count", "greater_than", 10))

    decision = evaluate_subscription_criteria(criteria, _event(count="eleven"))

    assert decision == {
        "criteria_version": 1,
        "outcome": "evaluation_error",
        "code": "field_type_mismatch",
    }


@pytest.mark.parametrize(
    "root",
    [
        _condition("payload.priority", "equals", "high"),
        _condition("event.body.priority;drop", "equals", "high"),
        _condition("event.body.priority", "unknown", "high"),
        _condition("event.body.priority", "in", []),
        _condition("event.body.priority", "contains", 1),
        _condition("event.body.priority", "greater_than", "ten"),
        _condition("event.body.priority", "exists", "unexpected"),
        {"kind": "not", "items": [_condition("event.type", "exists"), _condition("event.body", "exists")]},
    ],
)
def test_invalid_criteria_are_rejected_before_persistence(root):
    with pytest.raises(ValidationError):
        _criteria(root)


def test_depth_and_node_limits_are_enforced():
    too_deep = _condition("event.type", "exists")
    for _ in range(6):
        too_deep = {"kind": "not", "items": [too_deep]}

    with pytest.raises(ValidationError, match="nesting"):
        _criteria(too_deep)

    with pytest.raises(ValidationError, match="more than 50"):
        _criteria(
            {
                "kind": "all",
                "items": [
                    _condition(f"event.body.field{index}", "exists")
                    for index in range(50)
                ],
            }
        )


def test_invalid_persisted_criteria_fails_closed():
    decision = evaluate_subscription_criteria(
        {"version": 99, "root": _condition("event.type", "exists")},
        _event(),
    )

    assert decision == {
        "criteria_version": 99,
        "outcome": "evaluation_error",
        "code": "invalid_persisted_criteria",
    }


def test_schedule_fields_use_same_evaluator():
    event = _event(
        scheduled_time="2026-08-26T12:00:00+00:00",
        cron_expression="0 12 * * *",
        timezone="UTC",
    )
    criteria = _criteria(
        _condition("schedule.cron_expression", "equals", "0 12 * * *")
    )

    assert evaluate_subscription_criteria(criteria, event)["outcome"] == "matched"
