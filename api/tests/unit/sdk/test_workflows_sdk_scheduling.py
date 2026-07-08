"""SDK-level validation for scheduled execute and cancel."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bifrost._context import clear_execution_context, set_execution_context
from bifrost._execution_context import ExecutionContext, Organization
from bifrost.workflows import workflows


def _mock_post_response(json_body: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = lambda: json_body
    resp.headers = {}
    return resp


@pytest.mark.asyncio
async def test_execute_with_scheduled_at_includes_field():
    fake = MagicMock()
    fake.post = AsyncMock(
        return_value=_mock_post_response(
            {"execution_id": "e1", "status": "Scheduled"}
        )
    )
    run_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    with patch("bifrost.workflows.get_client", return_value=fake):
        eid = await workflows.execute("wf", scheduled_at=run_at)

    assert eid == "e1"
    payload = fake.post.await_args.kwargs["json"]
    assert payload["scheduled_at"] == run_at.isoformat()


@pytest.mark.asyncio
async def test_execute_with_delay_seconds_includes_field():
    fake = MagicMock()
    fake.post = AsyncMock(
        return_value=_mock_post_response({"execution_id": "e1"})
    )

    with patch("bifrost.workflows.get_client", return_value=fake):
        await workflows.execute("wf", delay_seconds=60)

    payload = fake.post.await_args.kwargs["json"]
    assert payload["delay_seconds"] == 60


@pytest.mark.asyncio
async def test_execute_rejects_naive_scheduled_at():
    with pytest.raises(ValueError, match="timezone"):
        await workflows.execute(
            "wf", scheduled_at=datetime.now() + timedelta(minutes=5)
        )


@pytest.mark.asyncio
async def test_execute_rejects_both_fields():
    run_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    with pytest.raises(ValueError, match="mutually exclusive"):
        await workflows.execute(
            "wf", scheduled_at=run_at, delay_seconds=60
        )


@pytest.mark.asyncio
async def test_cancel_calls_endpoint():
    fake = MagicMock()
    fake.post = AsyncMock(
        return_value=_mock_post_response({"status": "Cancelled"})
    )
    with patch("bifrost.workflows.get_client", return_value=fake):
        await workflows.cancel("exec-1")

    fake.post.assert_awaited_once()
    url = fake.post.await_args.args[0]
    assert url == "/api/workflows/executions/exec-1/cancel"


@pytest.mark.asyncio
async def test_execute_includes_solution_from_execution_context():
    fake = MagicMock()
    fake.post = AsyncMock(return_value=_mock_post_response({"execution_id": "e1"}))
    org = Organization(id="11111111-1111-1111-1111-111111111111", name="Org")
    solution_id = "22222222-2222-2222-2222-222222222222"
    ctx = ExecutionContext(
        user_id="u",
        email="u@example.com",
        name="User",
        scope=org.id,
        organization=org,
        is_platform_admin=True,
        is_function_key=False,
        execution_id="exec",
        solution_id=solution_id,
    )
    set_execution_context(ctx)
    try:
        with patch("bifrost.workflows.get_client", return_value=fake):
            await workflows.execute("workflows/child.py::main")
    finally:
        clear_execution_context()

    payload = fake.post.await_args.kwargs["json"]
    assert payload["solution_id"] == solution_id
