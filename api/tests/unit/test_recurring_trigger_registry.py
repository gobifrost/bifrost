"""Recurring trigger registry validation (no DB)."""

import pytest

from shared.recurring_trigger_registry import (
    TriggerDefinitionError,
    validate_overlap_policy,
    validate_review_params,
    validate_suite_params,
    validate_trigger_definition,
)


def test_review_params_default_lookback():
    params = validate_review_params({"review_id": "11111111-1111-1111-1111-111111111111"})
    assert params == {
        "review_id": "11111111-1111-1111-1111-111111111111",
        "lookback_days": 7,
    }


@pytest.mark.parametrize("bad", [0, 31, -1, "weeks", None, 2.5])
def test_review_params_reject_bad_lookback(bad):
    with pytest.raises(TriggerDefinitionError):
        validate_review_params({
            "review_id": "11111111-1111-1111-1111-111111111111",
            "lookback_days": bad,
        })


@pytest.mark.parametrize("bad", [None, "", "not-a-uuid", 12345])
def test_review_params_reject_bad_review_id(bad):
    with pytest.raises(TriggerDefinitionError):
        validate_review_params({"review_id": bad})


def test_review_params_reject_non_object():
    with pytest.raises(TriggerDefinitionError):
        validate_review_params(["review_id"])  # type: ignore[arg-type]


def test_overlap_skip_only():
    validate_overlap_policy("skip")
    with pytest.raises(TriggerDefinitionError):
        validate_overlap_policy("queue")
    with pytest.raises(TriggerDefinitionError):
        validate_overlap_policy("replace")


@pytest.mark.parametrize("bad", ["", "not cron", "* * * * * *", "61 * * * *"])
def test_invalid_cron_rejected(bad):
    with pytest.raises(TriggerDefinitionError):
        validate_trigger_definition(
            operation_type="agent_review",
            operation_params={"review_id": "11111111-1111-1111-1111-111111111111"},
            cron_expression=bad,
            timezone_name="UTC",
            overlap_policy="skip",
        )


def test_invalid_timezone_rejected():
    with pytest.raises(TriggerDefinitionError):
        validate_trigger_definition(
            operation_type="agent_review",
            operation_params={"review_id": "11111111-1111-1111-1111-111111111111"},
            cron_expression="* * * * *",
            timezone_name="Mars/Olympus",
            overlap_policy="skip",
        )


def test_unknown_operation_rejected():
    with pytest.raises(TriggerDefinitionError) as exc_info:
        validate_trigger_definition(
            operation_type="agent_tuning",
            operation_params={},
            cron_expression="* * * * *",
            timezone_name="UTC",
            overlap_policy="skip",
        )
    assert exc_info.value.code == "unknown_operation"


def test_evaluation_suite_registered_with_bounds():
    params = validate_suite_params(
        {
            "suite_id": "11111111-1111-1111-1111-111111111111",
            "candidate_ids": [],
            "profile_ids": ["22222222-2222-2222-2222-222222222222"],
        }
    )
    assert params == {
        "suite_id": "11111111-1111-1111-1111-111111111111",
        "candidate_ids": [],
        "profile_ids": ["22222222-2222-2222-2222-222222222222"],
        "repetitions_override": None,
    }
    params = validate_suite_params(
        {
            "suite_id": "11111111-1111-1111-1111-111111111111",
            "candidate_ids": ["33333333-3333-3333-3333-333333333333"],
            "profile_ids": ["22222222-2222-2222-2222-222222222222"],
            "repetitions_override": 3,
        }
    )
    assert params["repetitions_override"] == 3


@pytest.mark.parametrize(
    "params",
    [
        {"suite_id": "not-a-uuid", "profile_ids": ["22222222-2222-2222-2222-222222222222"]},
        {"profile_ids": ["22222222-2222-2222-2222-222222222222"]},
        {"suite_id": "11111111-1111-1111-1111-111111111111", "profile_ids": []},
        {
            "suite_id": "11111111-1111-1111-1111-111111111111",
            "profile_ids": ["22222222-2222-2222-2222-222222222222"] * 11,
        },
        {
            "suite_id": "11111111-1111-1111-1111-111111111111",
            "profile_ids": ["22222222-2222-2222-2222-222222222222"],
            "repetitions_override": 11,
        },
        {
            "suite_id": "11111111-1111-1111-1111-111111111111",
            "profile_ids": ["22222222-2222-2222-2222-222222222222"],
            "candidate_ids": "not-a-list",
        },
    ],
)
def test_suite_params_reject_bad_shapes(params):
    with pytest.raises(TriggerDefinitionError):
        validate_suite_params(params)


def test_trigger_definition_accepts_suite_operation():
    params = validate_trigger_definition(
        operation_type="agent_evaluation_suite",
        operation_params={
            "suite_id": "11111111-1111-1111-1111-111111111111",
            "profile_ids": ["22222222-2222-2222-2222-222222222222"],
        },
        cron_expression="* * * * *",
        timezone_name="UTC",
        overlap_policy="skip",
    )
    assert params["suite_id"] == "11111111-1111-1111-1111-111111111111"
