"""The consumer writes the lineage its claimed run carries onto the execution row."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.services.authorization.tokens import RunCredentials
from src.services.execution.process_pool import CredentialRoute

MODULE = "src.jobs.consumers.workflow_execution"
EXECUTION_ID = "00000000-0000-0000-0000-000000000001"
RUN_USER_ID = "00000000-0000-0000-0000-0000000000aa"
MESSAGE = {"execution_id": EXECUTION_ID, "code": "eA==", "script_name": "inline"}


def _claimed(**extra) -> dict:
    return {
        "execution_id": EXECUTION_ID,
        "workflow_id": None,
        "script_name": "inline",
        "parameters": {},
        "org_id": None,
        "user_id": RUN_USER_ID,
        "user_name": "Person",
        "user_email": "p@example.com",
        "lineage": None,
        **extra,
    }


def _consumer(route: CredentialRoute = CredentialRoute(1)) -> WorkflowExecutionConsumer:
    consumer = WorkflowExecutionConsumer.__new__(WorkflowExecutionConsumer)
    consumer._redis_client = AsyncMock()
    consumer._pool = AsyncMock()
    # A legacy route by default: its token is signed without a database read.
    consumer._pool.credential_route = Mock(return_value=route)
    return consumer


async def _created_with(claimed: dict, *, queue_error: Exception | None = None) -> dict:
    with (
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch(f"{MODULE}.publish_execution_update", new=AsyncMock()),
        patch(f"{MODULE}.publish_history_update", new=AsyncMock()),
        patch("src.services.execution.queue_tracker.publish_queue_positions", new=AsyncMock(side_effect=queue_error)),
    ):
        consumer = _consumer()
        await consumer.process_message(MESSAGE, claimed)  # type: ignore[arg-type]
    consumer._pool.route_execution.assert_awaited_once()
    assert create.await_args is not None
    return dict(create.await_args.kwargs)


@pytest.mark.asyncio
async def test_row_gets_the_claimed_lineage() -> None:
    lineage = {
        "run_user_id": RUN_USER_ID,
        "started_by_user_id": RUN_USER_ID,
        "root_execution_id": EXECUTION_ID,
    }

    assert (await _created_with(_claimed(lineage=lineage)))["lineage"] == lineage


@pytest.mark.asyncio
async def test_row_gets_the_claimed_impersonation_provenance() -> None:
    operator_id = "00000000-0000-0000-0000-0000000000bb"
    session_id = "00000000-0000-0000-0000-0000000000cc"

    created = await _created_with(
        _claimed(
            impersonation_provenance={
                "impersonated_by_user_id": operator_id,
                "impersonation_session_id": session_id,
            }
        )
    )

    assert created["impersonation_provenance"] == {
        "impersonated_by_user_id": operator_id,
        "impersonation_session_id": session_id,
    }


@pytest.mark.asyncio
async def test_a_run_that_moved_on_is_discarded_and_wakes_its_sync_caller() -> None:
    class _DbContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    session = AsyncMock()
    with (
        patch(f"{MODULE}.get_db_context", return_value=_DbContext()),
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch(
            "src.services.execution.workflow_dispatch.notify_discarded_sync_run",
            new=AsyncMock(),
        ) as notify,
        patch("src.services.execution.queue_tracker.publish_queue_positions", new=AsyncMock()),
    ):
        consumer = _consumer()
        await consumer.process_message({**MESSAGE, "sync": True}, None)

    create.assert_not_awaited()
    consumer._pool.route_execution.assert_not_awaited()
    notify.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("queue unavailable"), ConnectionError("queue unavailable")])
async def test_ui_queue_tracking_failure_does_not_abandon_a_claimed_workflow(error) -> None:
    created = await _created_with(_claimed(), queue_error=error)

    assert created["execution_id"] == EXECUTION_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "credential_version", "refresh_token"),
    [(CredentialRoute(2), 2, "run-refresh"), (CredentialRoute(1, 7), None, None)],
    ids=["claims route", "legacy launcher"],
)
async def test_credentials_are_minted_for_the_route_chosen_first(
    route: CredentialRoute, credential_version: int | None, refresh_token: str | None
) -> None:
    issued = RunCredentials("access", refresh_token, datetime.now(timezone.utc), credential_version)
    with (
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch(f"{MODULE}.publish_execution_update", new=AsyncMock()),
        patch(f"{MODULE}.publish_history_update", new=AsyncMock()),
        patch("src.services.execution.queue_tracker.publish_queue_positions", new=AsyncMock()),
        patch(
            "src.services.authorization.tokens.issue_run_credentials", new=AsyncMock(return_value=issued)
        ) as mint,
    ):
        consumer = _consumer(route)
        await consumer.process_message(MESSAGE, _claimed())  # type: ignore[arg-type]

    assert create.await_args.kwargs["credential_version"] == credential_version
    assert mint.await_args.kwargs["protocol"] == route.protocol
    routed = consumer._pool.route_execution.await_args.kwargs
    assert routed["credential_route"] == route
    assert routed["context"]["engine_token"] == "access"
    assert routed["context"].get("engine_refresh_token") == refresh_token
