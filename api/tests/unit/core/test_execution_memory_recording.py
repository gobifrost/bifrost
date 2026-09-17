"""Regression tests for trustworthy workflow memory recording.

Failed executions are often the highest-memory samples, so the recording
path must preserve their resource metrics instead of dropping them, route
org samples to the org row exactly once, and never let an unknown sample
move a peak column.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.core.metrics import _parse_org_uuid, _upsert_daily_metrics, update_daily_metrics
from src.models.enums import ExecutionStatus
from src.services.execution.simple_worker import _capture_failure_metrics


def test_capture_failure_metrics_reports_pss_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.services.execution.simple_worker._get_pss_bytes", lambda: 200 * 1024
    )
    metrics = _capture_failure_metrics(100 * 1024)

    assert metrics["peak_memory_bytes"] == 100 * 1024
    assert metrics["cpu_total_seconds"] >= 0.0


def test_capture_failure_metrics_uses_none_for_unknown_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.services.execution.simple_worker._get_pss_bytes", lambda: 0
    )
    metrics = _capture_failure_metrics(100 * 1024)

    assert metrics["peak_memory_bytes"] is None


def test_parse_org_uuid_accepts_bare_and_prefixed_forms() -> None:
    raw = str(uuid4())
    assert _parse_org_uuid(raw) is not None
    assert _parse_org_uuid(f"ORG:{raw}") == _parse_org_uuid(raw)
    assert _parse_org_uuid(None) is None
    assert _parse_org_uuid("not-a-uuid") is None


@pytest.mark.asyncio
async def test_update_daily_metrics_routes_bare_uuid_org_once_each() -> None:
    session = AsyncMock()
    org_id = str(uuid4())

    with patch(
        "src.core.metrics._upsert_daily_metrics",
        new_callable=AsyncMock,
    ) as upsert:
        await update_daily_metrics(
            org_id=org_id,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=10,
            peak_memory_bytes=20,
            cpu_total_seconds=0.1,
            db=session,
        )

    assert upsert.await_count == 2
    first_org = upsert.await_args_list[0].args[2]
    second_org = upsert.await_args_list[1].args[2]
    assert str(first_org) == org_id
    assert second_org is None


@pytest.mark.asyncio
async def test_update_daily_metrics_writes_global_row_once_when_orgless() -> None:
    session = AsyncMock()

    with patch(
        "src.core.metrics._upsert_daily_metrics",
        new_callable=AsyncMock,
    ) as upsert:
        await update_daily_metrics(
            org_id=None,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=10,
            db=session,
        )

    assert upsert.await_count == 1
    assert upsert.await_args_list[0].args[2] is None


def _conflict_clause(stmt) -> str:
    compiled = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    return compiled.split("ON CONFLICT", 1)[1]


@pytest.mark.asyncio
async def test_upsert_skips_peak_columns_for_unknown_samples() -> None:
    session = AsyncMock()

    await _upsert_daily_metrics(
        db=session,
        today=date.today(),
        org_id=None,
        status=ExecutionStatus.FAILED.value,
        duration_ms=5,
        peak_memory_bytes=None,
        cpu_total_seconds=None,
        time_saved=0,
        value=0.0,
    )

    conflict = _conflict_clause(session.execute.await_args.args[0])
    assert "peak_memory_bytes" not in conflict
    assert "peak_cpu_seconds" not in conflict
    assert "total_memory_bytes" in conflict


@pytest.mark.asyncio
async def test_upsert_moves_peaks_for_known_samples() -> None:
    session = AsyncMock()

    await _upsert_daily_metrics(
        db=session,
        today=date.today(),
        org_id=None,
        status=ExecutionStatus.SUCCESS.value,
        duration_ms=5,
        peak_memory_bytes=1024,
        cpu_total_seconds=0.5,
        time_saved=0,
        value=0.0,
    )

    conflict = _conflict_clause(session.execute.await_args.args[0])
    assert "peak_memory_bytes" in conflict
    assert "peak_cpu_seconds" in conflict


@pytest.mark.asyncio
async def test_process_failure_forwards_reported_metrics() -> None:
    from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

    durable_session = AsyncMock()
    durable_context = MagicMock()
    durable_context.__aenter__ = AsyncMock(return_value=durable_session)
    durable_context.__aexit__ = AsyncMock(return_value=None)
    metrics_context = MagicMock()
    metrics_context.__aenter__ = AsyncMock(return_value=AsyncMock())
    metrics_context.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(side_effect=[durable_context, metrics_context])

    with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
        consumer = WorkflowExecutionConsumer()
        consumer._redis_client = AsyncMock()
        consumer._redis_client.get_active_execution.return_value = {
            "workflow_id": str(uuid4()),
            "workflow_name": "memory_workflow",
            "org_id": str(uuid4()),
            "user_id": str(uuid4()),
            "user_email": "test@example.com",
            "user_name": "Test User",
            "sync": False,
        }

        with (
            patch(
                "src.core.database.get_session_factory",
                return_value=session_factory,
            ),
            patch(
                "src.jobs.consumers.workflow_execution.update_execution",
                new_callable=AsyncMock,
            ) as update_execution,
            patch(
                "bifrost._sync.flush_pending_changes",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "src.core.metrics.update_daily_metrics",
                new_callable=AsyncMock,
            ) as daily_metrics,
            patch(
                "src.jobs.consumers.workflow_execution.publish_execution_update",
                new_callable=AsyncMock,
            ),
            patch(
                "src.jobs.consumers.workflow_execution.publish_history_update",
                new_callable=AsyncMock,
            ),
            patch(
                "src.core.cache.cleanup_execution_cache",
                new_callable=AsyncMock,
            ),
            patch(
                "src.services.events.builtins.emit_workflow_failure_events",
                new_callable=AsyncMock,
            ),
        ):
            await consumer._process_failure(
                str(uuid4()),
                {
                    "success": False,
                    "error": "boom",
                    "error_type": "TimeoutError",
                    "duration_ms": 123,
                    "sync": False,
                    "metrics": {
                        "peak_memory_bytes": 4242,
                        "cpu_total_seconds": 1.5,
                    },
                },
            )

    assert update_execution.await_args.kwargs["metrics"] == {
        "peak_memory_bytes": 4242,
        "cpu_total_seconds": 1.5,
    }
    assert daily_metrics.await_args.kwargs["peak_memory_bytes"] == 4242
    assert daily_metrics.await_args.kwargs["cpu_total_seconds"] == 1.5
