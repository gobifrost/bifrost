"""Contracts for Bifrost's built-in event payloads."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest


@pytest.mark.asyncio
async def test_workflow_completed_event_contains_only_completion_metadata() -> None:
    from src.services.events.builtins import emit_workflow_completed_event

    completed_at = datetime(2026, 10, 28, 15, 30, tzinfo=timezone.utc)
    with patch(
        "src.services.events.builtins._emit", new_callable=AsyncMock
    ) as emit:
        await emit_workflow_completed_event(
            workflow_id=UUID("00000000-0000-0000-0000-000000000001"),
            workflow_name="sync_tickets",
            execution_id=UUID("00000000-0000-0000-0000-000000000002"),
            organization_id=UUID("00000000-0000-0000-0000-000000000003"),
            user_id=UUID("00000000-0000-0000-0000-000000000004"),
            user_email="operator@example.com",
            user_name="Operator",
            started_at=datetime(2026, 10, 28, 15, 29, tzinfo=timezone.utc),
            completed_at=completed_at,
            duration_ms=123,
            attempt=1,
            max_attempts=1,
            trigger_event={
                "id": "00000000-0000-0000-0000-000000000005",
                "type": "ticket.created",
            },
        )

    topic, body = emit.await_args.args
    assert topic == "workflow.completed"
    assert emit.await_args.kwargs == {
        "organization_id": UUID("00000000-0000-0000-0000-000000000003"),
        "triggered_by": "00000000-0000-0000-0000-000000000004",
    }
    assert body == {
        "schema_version": 1,
        "occurred_at": completed_at.isoformat(),
        "organization": {
            "id": "00000000-0000-0000-0000-000000000003",
            "name": None,
        },
        "actor": {
            "type": "user",
            "id": "00000000-0000-0000-0000-000000000004",
            "email": "operator@example.com",
            "name": "Operator",
        },
        "workflow": {
            "id": "00000000-0000-0000-0000-000000000001",
            "name": "sync_tickets",
        },
        "execution": {
            "id": "00000000-0000-0000-0000-000000000002",
            "status": "Success",
            "attempt": 1,
            "max_attempts": 1,
            "started_at": "2026-10-28T15:29:00+00:00",
            "completed_at": completed_at.isoformat(),
            "duration_ms": 123,
        },
        "trigger": {
            "type": "event",
            "event_type": "ticket.created",
            "event_id": "00000000-0000-0000-0000-000000000005",
        },
    }
