"""Public workflow-conversion contracts keep review state explicit."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from shared.models import (
    WorkflowConversionAnalyzeRequest,
    WorkflowConversionApplyRequest,
    WorkflowConversionSelection,
)
from src.models.contracts.access_list import AccessClass
from shared import workflow_conversions as conversion


def test_analyze_request_rejects_an_explicit_empty_selection() -> None:
    with pytest.raises(ValidationError, match="workflow_ids"):
        WorkflowConversionAnalyzeRequest(workflow_ids=[])


def test_apply_selection_requires_review_of_unknown_source() -> None:
    selection = WorkflowConversionSelection(
        workflow_id=uuid4(),
        source_fingerprint="a" * 64,
        identity_strategy="dedicated",
        acknowledged_unknowns=True,
        acknowledged_broad_identity=False,
    )
    request = WorkflowConversionApplyRequest(selections=[selection])

    assert request.selections == [selection]


def test_observed_own_resource_operation_needs_review_instead_of_granting_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ownership-only operation must not inflate a converted grant set."""
    from src.services.authorization import enforce

    monkeypatch.setattr(
        enforce,
        "entry_for_operation",
        lambda _operation: type(
            "Entry",
            (),
            {
                "access_class": AccessClass.OWN_PRIVATE_AGENT,
                "permission": "agents.readwrite.all",
            },
        )(),
    )

    permissions, unknowns = conversion._permissions_for_observed_operations(
        ["agents.update"]
    )

    assert permissions == []
    assert unknowns == [
        "Observed operation agents.update depends on ownership and needs review"
    ]


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


def test_keep_current_identity_with_an_additional_role_is_broad() -> None:
    assert conversion._broad_identity_warning(
        UUID("00000000-0000-0000-0000-000000000006"),
        [uuid4()],
        [],
        [],
    )
