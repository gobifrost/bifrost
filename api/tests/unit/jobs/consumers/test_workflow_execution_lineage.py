"""The consumer writes the lineage its claimed run carries onto the execution row."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.jobs.consumers.workflow_execution import (
    WorkflowExecutionConsumer,
    _legacy_unattended_pending_actor,
)
from src.services.authorization.tokens import RunCredentials
from src.services.execution.process_pool import CredentialRoute

MODULE = "src.jobs.consumers.workflow_execution"
EXECUTION_ID = "00000000-0000-0000-0000-000000000001"
RUN_USER_ID = "00000000-0000-0000-0000-0000000000aa"
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
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


async def _created_with(claimed: dict) -> dict:
    with (
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch(f"{MODULE}.publish_execution_update", new=AsyncMock()),
        patch(f"{MODULE}.publish_history_update", new=AsyncMock()),
        patch(
            "src.services.execution.queue_tracker.request_queue_position_refresh",
            new=Mock(),
        ) as refresh,
    ):
        consumer = _consumer()
        await consumer.process_message(MESSAGE, claimed)  # type: ignore[arg-type]
    consumer._pool.route_execution.assert_awaited_once()
    refresh.assert_called_once_with()
    assert create.await_args is not None
    return dict(create.await_args.kwargs)


@pytest.mark.asyncio
async def test_authorized_source_digest_reaches_the_worker():
    workflow_id = "00000000-0000-0000-0000-000000000002"
    digest = "a" * 64
    metadata = {
        "name": "pinned", "function_name": "run", "path": "workflows/pinned.py",
        "type": "workflow", "cache_ttl_seconds": 0, "timeout_seconds": 300,
        "time_saved": 0, "value": 0, "organization_id": None, "solution_id": None,
        "content_hash": digest,
    }
    with (
        patch(f"{MODULE}.create_execution", new=AsyncMock()),
        patch(f"{MODULE}.publish_execution_update", new=AsyncMock()),
        patch(f"{MODULE}.publish_history_update", new=AsyncMock()),
        patch("src.services.execution.queue_tracker.request_queue_position_refresh", new=Mock()),
    ):
        consumer = _consumer()
        await consumer.process_message(
            {"execution_id": EXECUTION_ID, "workflow_id": workflow_id,
             "dispatch_metadata": metadata},
            _claimed(workflow_id=workflow_id, script_name=None),
        )  # type: ignore[arg-type]

    consumer._pool.route_execution.assert_awaited_once()
    assert consumer._pool.route_execution.await_args.kwargs["context"]["content_hash"] == digest


@pytest.mark.asyncio
async def test_row_gets_the_claimed_lineage() -> None:
    lineage = {
        "run_user_id": RUN_USER_ID,
        "started_by_user_id": RUN_USER_ID,
        "root_execution_id": EXECUTION_ID,
    }

    created = await _created_with(_claimed(lineage=lineage))

    assert created["lineage"] == lineage
    assert created["user_id"] == RUN_USER_ID


@pytest.mark.asyncio
async def test_historic_unattended_pending_run_uses_its_durable_identity_not_pending_flags() -> None:
    lineage = {
        "run_user_id": RUN_USER_ID,
        "started_by_user_id": RUN_USER_ID,
        "root_execution_id": EXECUTION_ID,
    }
    actor = SimpleNamespace(
        user_id=RUN_USER_ID,
        email="identity@example.test",
        name="Scheduled Identity",
        is_platform_admin=False,
    )
    issued = RunCredentials("access", None, datetime.now(timezone.utc), 2)
    with (
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch(f"{MODULE}.publish_execution_update", new=AsyncMock()),
        patch(f"{MODULE}.publish_history_update", new=AsyncMock()),
        patch(f"{MODULE}._legacy_unattended_pending_actor", new=AsyncMock(return_value=actor)),
        patch(
            "src.services.authorization.tokens.issue_run_credentials", new=AsyncMock(return_value=issued)
        ) as mint,
        patch(
            "src.services.execution.queue_tracker.request_queue_position_refresh",
            new=Mock(),
        ),
    ):
        consumer = _consumer(CredentialRoute(2))
        await consumer.process_message(
            MESSAGE,
            _claimed(
                user_id=SYSTEM_USER_ID,
                user_name="System",
                user_email="engine@bifrost.internal",
                is_platform_admin=True,
                lineage=lineage,
            ),
        )  # type: ignore[arg-type]

    assert create.await_args.kwargs["user_id"] == RUN_USER_ID
    assert create.await_args.kwargs["user_name"] == "Scheduled Identity"
    assert mint.await_count == 1
    routed = consumer._pool.route_execution.await_args.kwargs["context"]
    assert routed["caller"] == {
        "user_id": RUN_USER_ID,
        "email": "identity@example.test",
        "name": "Scheduled Identity",
    }
    assert routed["is_platform_admin"] is False
    assert routed["original_user_id"] == RUN_USER_ID


@pytest.mark.asyncio
async def test_historic_unattended_pending_run_without_proven_identity_fails_before_minting() -> None:
    with (
        patch(f"{MODULE}.create_execution", new=AsyncMock()),
        patch(f"{MODULE}.update_execution", new=AsyncMock()),
        patch(f"{MODULE}.publish_execution_update", new=AsyncMock()),
        patch(f"{MODULE}.publish_history_update", new=AsyncMock()),
        patch(
            f"{MODULE}._legacy_unattended_pending_actor",
            new=AsyncMock(side_effect=ValueError("legacy pending lineage is incomplete")),
        ),
        patch("src.services.authorization.tokens.issue_run_credentials", new=AsyncMock()) as mint,
        patch(
            "src.services.execution.queue_tracker.request_queue_position_refresh",
            new=Mock(),
        ),
    ):
        consumer = _consumer(CredentialRoute(2))
        await consumer.process_message(
            MESSAGE,
            _claimed(user_id=SYSTEM_USER_ID, lineage=None),
        )  # type: ignore[arg-type]

    mint.assert_not_awaited()
    consumer._pool.route_execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_unattended_actor_uses_current_identity_and_roles() -> None:
    identity = SimpleNamespace(
        id=UUID(RUN_USER_ID),
        email="identity@example.test",
        name="Scheduled Identity",
        is_system=False,
        identity_kind="org_default",
    )
    db = AsyncMock()
    db.get.return_value = identity
    with patch(
        "src.services.authorization.explain.load_run_user",
        new=AsyncMock(return_value=SimpleNamespace(is_platform_admin=False)),
    ):
        actor = await _legacy_unattended_pending_actor(
            db,
            _claimed(
                user_id=SYSTEM_USER_ID,
                is_platform_admin=True,
                lineage={
                    "run_user_id": RUN_USER_ID,
                    "started_by_user_id": RUN_USER_ID,
                    "root_execution_id": EXECUTION_ID,
                },
            ),  # type: ignore[arg-type]
        )

    assert actor is not None
    assert actor.user_id == RUN_USER_ID
    assert actor.email == "identity@example.test"
    assert actor.is_platform_admin is False


@pytest.mark.asyncio
async def test_legacy_unattended_actor_refuses_absent_identity() -> None:
    db = AsyncMock()
    db.get.return_value = None

    with pytest.raises(ValueError, match="no current run identity"):
        await _legacy_unattended_pending_actor(
            db,
            _claimed(
                user_id=SYSTEM_USER_ID,
                lineage={
                    "run_user_id": RUN_USER_ID,
                    "started_by_user_id": RUN_USER_ID,
                    "root_execution_id": EXECUTION_ID,
                },
            ),  # type: ignore[arg-type]
        )


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
        patch(
            "src.services.execution.queue_tracker.request_queue_position_refresh",
            new=Mock(),
        ) as refresh,
    ):
        consumer = _consumer()
        await consumer.process_message({**MESSAGE, "sync": True}, None)

    create.assert_not_awaited()
    consumer._pool.route_execution.assert_not_awaited()
    notify.assert_awaited_once()
    refresh.assert_not_called()


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
        patch(
            "src.services.execution.queue_tracker.request_queue_position_refresh",
            new=Mock(),
        ) as refresh,
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
    assert routed["context"]["execution_origin"] == "agent"
    assert routed["context"]["agent_id"] == "00000000-0000-0000-0000-0000000000bb"
    assert routed["context"]["original_user_is_platform_admin"] is False
    assert routed["context"]["original_user_id"] == RUN_USER_ID
    assert routed["context"]["engine_token"] == "access"
    assert routed["context"].get("engine_refresh_token") == refresh_token
    refresh.assert_called_once_with()


@pytest.fixture(autouse=True)
def current_runtime_authority():
    @asynccontextmanager
    async def database():
        yield AsyncMock()

    from shared.execution_provenance import ExecutionProvenance

    authority = SimpleNamespace(
        execution_origin="agent",
        agent_id=UUID("00000000-0000-0000-0000-0000000000bb"),
        run_user=SimpleNamespace(user_id=RUN_USER_ID, is_platform_admin=False),
    )
    with (
        patch(f"{MODULE}.get_db_context", database),
        patch("src.services.authorization.runtime.load_runtime_authority", new=AsyncMock(return_value=authority)),
        patch("src.services.authorization.runtime.load_execution_provenance", new=AsyncMock(return_value=ExecutionProvenance("agent", authority.agent_id))),
    ):
        yield
