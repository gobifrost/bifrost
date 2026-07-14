"""Regression tests for lightweight execution-history responses."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4


def _summary_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        workflow_name="large_result_workflow",
        workflow_id=uuid4(),
        organization_id=None,
        organization=None,
        form_id=None,
        executed_by=uuid4(),
        executed_by_name="Test User",
        executed_by_user=None,
        status="Success",
        result_type="json",
        error_message=None,
        duration_ms=118,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        scheduled_at=None,
        session_id=None,
    )


def test_summary_does_not_access_large_payload_fields() -> None:
    """History serialization must work with result/input columns unloaded."""
    from src.routers.executions import ExecutionRepository

    summary = ExecutionRepository._to_summary(_summary_row())  # type: ignore[arg-type]

    assert summary.duration_ms == 118
    assert summary.result_type == "json"


def test_summary_omits_payload_fields_entirely() -> None:
    """List items must not carry payload keys at all — absent, not null.

    Returning `result: null` / `input_data: {}` reads as "this execution had
    no result", which is a lie for large executions. Omission makes the
    summary-vs-detail split explicit for SDK and API consumers.
    """
    from src.models.contracts.executions import ExecutionSummary
    from src.routers.executions import ExecutionRepository

    summary = ExecutionRepository._to_summary(_summary_row())  # type: ignore[arg-type]

    assert isinstance(summary, ExecutionSummary)
    payload = summary.model_dump()
    for field in ("input_data", "result", "variables", "execution_context", "logs"):
        assert field not in payload


def test_full_model_still_carries_payload_fields() -> None:
    """The single-execution model keeps the payload contract intact."""
    from src.models.contracts.executions import WorkflowExecution

    fields = set(WorkflowExecution.model_fields)
    assert {"input_data", "result", "variables", "execution_context", "logs"} <= fields
