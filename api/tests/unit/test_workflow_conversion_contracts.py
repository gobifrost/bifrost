"""Public workflow-conversion contracts keep review state explicit."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.models import (
    WorkflowConversionAnalyzeRequest,
    WorkflowConversionApplyRequest,
    WorkflowConversionSelection,
)
from shared import workflow_conversions as conversion


def test_analyze_request_rejects_an_explicit_empty_selection() -> None:
    with pytest.raises(ValidationError, match="workflow_ids"):
        WorkflowConversionAnalyzeRequest(workflow_ids=[])


def test_apply_selection_only_records_source_review_acknowledgement() -> None:
    selection = WorkflowConversionSelection(
        workflow_id=uuid4(),
        source_fingerprint="a" * 64,
        acknowledged_unknowns=True,
    )
    request = WorkflowConversionApplyRequest(selections=[selection])

    assert request.selections == [selection]


@pytest.mark.parametrize("field", ["identity_strategy", "acknowledged_broad_identity"])
def test_apply_selection_rejects_removed_identity_controls(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        WorkflowConversionSelection(
            workflow_id=uuid4(),
            source_fingerprint="a" * 64,
            **{field: "dedicated" if field == "identity_strategy" else True},
        )


def test_recorded_own_resource_operation_does_not_add_elevated_authority() -> None:
    """Own-resource admission stays separate from declared named powers."""
    permissions, unknowns = conversion._permissions_for_observed_operations(
        ["agents.update"]
    )

    assert permissions == []
    assert unknowns == []


def test_followup_dedupe_key_is_stable_per_analysis_request_and_job_type() -> None:
    request = WorkflowConversionApplyRequest(
        selections=[
            WorkflowConversionSelection(
                workflow_id=uuid4(), source_fingerprint="b" * 64
            )
        ]
    )
    analysis_id = uuid4()

    first = conversion._followup_dedupe_key(
        analysis_id, "workflow.permissions.apply", request
    )
    same = conversion._followup_dedupe_key(
        analysis_id, "workflow.permissions.apply", request
    )
    cutover = conversion._followup_dedupe_key(
        analysis_id, "workflow.permissions.cutover", request
    )

    assert first == same
    assert first != cutover
