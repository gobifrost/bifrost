"""Regression tests for lightweight execution-history responses."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4


def test_execution_summary_does_not_access_large_payload_fields() -> None:
    """History serialization must work with result/input columns unloaded."""
    from src.routers.executions import ExecutionRepository

    execution = SimpleNamespace(
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

    summary = ExecutionRepository._to_pydantic(  # type: ignore[arg-type]
        SimpleNamespace(), execution, include_payload=False
    )

    assert summary.input_data == {}
    assert summary.result is None
    assert summary.variables is None
    assert summary.execution_context is None
