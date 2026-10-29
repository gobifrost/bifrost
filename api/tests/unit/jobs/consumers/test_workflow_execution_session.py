"""Tests for WorkflowExecutionConsumer session management.

Validates that the consumer uses short-lived sessions (no persistent session).
"""

import asyncio

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest


class TestCompletionMetadataRecovery:
    """PostgreSQL must recover terminal bookkeeping after Redis data loss."""

    @pytest.mark.asyncio
    async def test_missing_active_lease_reconstructs_metadata_from_execution_row(self):
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        execution = MagicMock()
        execution.id = UUID("00000000-0000-0000-0000-000000000004")
        execution.workflow_id = UUID("00000000-0000-0000-0000-000000000001")
        execution.workflow_name = "long_scan"
        execution.organization_id = UUID("00000000-0000-0000-0000-000000000002")
        execution.executed_by = UUID("00000000-0000-0000-0000-000000000003")
        execution.executed_by_name = "Test User"

        query_result = MagicMock()
        query_result.one_or_none.return_value = (execution, "test@example.com")
        session = AsyncMock()
        session.execute.return_value = query_result

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._redis_client = AsyncMock()
            consumer._redis_client.get_active_execution.return_value = None

            metadata, recovered = await consumer._load_completion_metadata(
                str(execution.id), session
            )

        assert recovered is True
        assert metadata == {
            "execution_id": str(execution.id),
            "workflow_id": str(execution.workflow_id),
            "workflow_name": "long_scan",
            "org_id": str(execution.organization_id),
            "user_id": str(execution.executed_by),
            "user_name": "Test User",
            "user_email": "test@example.com",
            "sync": False,
            "event": None,
        }
        statement = str(session.execute.await_args.args[0])
        assert "executions.status IN" in statement

    @pytest.mark.asyncio
    async def test_active_lease_avoids_database_lookup(self):
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        active = {
            "execution_id": "00000000-0000-0000-0000-000000000004",
            "workflow_id": "00000000-0000-0000-0000-000000000001",
            "workflow_name": "long_scan",
            "org_id": "00000000-0000-0000-0000-000000000002",
            "user_id": "00000000-0000-0000-0000-000000000003",
            "user_name": "Test User",
            "user_email": "test@example.com",
            "sync": False,
            "event": None,
        }
        session = AsyncMock()

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._redis_client = AsyncMock()
            consumer._redis_client.get_active_execution.return_value = active

            metadata, recovered = await consumer._load_completion_metadata(
                active["execution_id"], session
            )

        assert metadata == active
        assert recovered is False
        session.execute.assert_not_awaited()


class TestConsumerSessionLifecycle:
    """Test that consumer no longer holds a persistent DB session."""

    @pytest.mark.asyncio
    async def test_no_get_db_session_method(self):
        """Consumer should not have _get_db_session() method (removed)."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        assert not hasattr(WorkflowExecutionConsumer, "_get_db_session")


class TestSuccessfulExecutionCompletionOrder:
    """Sync callers should not wait for derived aggregates or terminal fan-out."""

    @pytest.mark.asyncio
    async def test_sync_result_is_pushed_before_terminal_fan_out(self):
        """Wake the requester after the durable commit, before UI/cache work.

        Large workflow results make every extra serialization on this path
        requester-visible.  The terminal WebSocket event is only a status
        notification; clients fetch the persisted result through the result
        endpoint, so rebroadcasting the full payload is both redundant and
        expensive.
        """
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        call_order: list[str] = []
        durable_session = AsyncMock()
        durable_session.commit.side_effect = lambda: call_order.append(
            "durable_commit"
        )
        metrics_session = AsyncMock()
        metrics_session.commit.side_effect = lambda: call_order.append(
            "metrics_commit"
        )

        durable_context = MagicMock()
        durable_context.__aenter__ = AsyncMock(return_value=durable_session)
        durable_context.__aexit__ = AsyncMock(return_value=None)
        metrics_context = MagicMock()
        metrics_context.__aenter__ = AsyncMock(return_value=metrics_session)
        metrics_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock()
        session_factory.side_effect = [durable_context, metrics_context]

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._redis_client = AsyncMock()
            consumer._redis_client.get_active_execution.return_value = {
                "workflow_id": "00000000-0000-0000-0000-000000000001",
                "workflow_name": "large_result_workflow",
                "org_id": "00000000-0000-0000-0000-000000000002",
                "user_id": "00000000-0000-0000-0000-000000000003",
                "user_name": "Test User",
                "sync": True,
            }
            sync_woken = asyncio.Event()
            recovery_started = asyncio.Event()
            allow_recovery = asyncio.Event()

            def push_result(**_kwargs):
                call_order.append("push_result")
                sync_woken.set()

            async def block_recovery(*_args, **_kwargs):
                recovery_started.set()
                await allow_recovery.wait()

            consumer._redis_client.push_result.side_effect = push_result

            publish_execution = AsyncMock(
                side_effect=lambda *_args, **_kwargs: call_order.append(
                    "publish_execution"
                )
            )
            publish_history = AsyncMock(
                side_effect=lambda **_kwargs: call_order.append("publish_history")
            )
            cleanup_cache = AsyncMock(
                side_effect=lambda _execution_id: call_order.append("cleanup_cache")
            )
            receipt = MagicMock(count=1)
            stage_logs = AsyncMock(return_value=receipt)
            acknowledge_logs = AsyncMock()
            recover_logs = AsyncMock(side_effect=block_recovery)

            update_delivery = AsyncMock()

            with (
                patch(
                    "src.core.database.get_session_factory",
                    return_value=session_factory,
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.update_execution",
                    new_callable=AsyncMock,
                ),
                patch.object(
                    WorkflowExecutionConsumer,
                    "_read_persisted_completion_timing",
                    new=AsyncMock(return_value=(None, None)),
                ),
                patch(
                    "src.services.events.processor.update_delivery_from_execution",
                    new=update_delivery,
                ),
                patch(
                    "bifrost._logging.stage_logs_to_postgres",
                    new=stage_logs,
                ),
                patch("bifrost._logging.acknowledge_staged_logs", new=acknowledge_logs),
                patch("bifrost._logging.recover_terminal_execution_logs", new=recover_logs),
                patch(
                    "src.core.metrics.update_daily_metrics",
                    new=AsyncMock(
                        side_effect=lambda **_kwargs: call_order.append(
                            "daily_metrics"
                        )
                    ),
                ),
                patch(
                    "src.core.metrics.update_workflow_roi_daily",
                    new=AsyncMock(
                        side_effect=lambda **_kwargs: call_order.append(
                            "workflow_metrics"
                        )
                    ),
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.publish_execution_update",
                    publish_execution,
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.publish_history_update",
                    publish_history,
                ),
                patch("src.core.cache.cleanup_execution_cache", cleanup_cache),
                patch(
                    "src.services.events.builtins.emit_workflow_completed_event",
                    new_callable=AsyncMock,
                ),
            ):
                completion = asyncio.create_task(consumer._process_success(
                    "00000000-0000-0000-0000-000000000004",
                    {
                        "success": True,
                        "status": "Success",
                        "result": {"rows": [{"value": "x" * 1000}]},
                        "duration_ms": 123,
                        "sync": True,
                    },
                ))
                await asyncio.wait_for(sync_woken.wait(), timeout=1)
                await asyncio.wait_for(recovery_started.wait(), timeout=1)
                assert not completion.done()
                allow_recovery.set()
                await asyncio.gather(completion)

        assert call_order[:2] == ["durable_commit", "push_result"]
        assert call_order.index("push_result") < call_order.index("daily_metrics")
        assert call_order.index("push_result") < call_order.index("workflow_metrics")
        assert call_order.index("push_result") < call_order.index("metrics_commit")
        assert call_order.index("push_result") < call_order.index("publish_execution")
        assert call_order.index("push_result") < call_order.index("publish_history")
        assert call_order.index("push_result") < call_order.index("cleanup_cache")
        update_delivery.assert_not_awaited()
        stage_logs.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000004", durable_session
        )

        terminal_data = publish_execution.await_args.args[2]
        assert terminal_data == {"duration_ms": 123}

    @pytest.mark.asyncio
    async def test_workflow_completed_emits_after_commit_and_sync_wakeup(self):
        """The completion event observes the durable result and wakes sync callers first."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        call_order: list[str] = []
        durable_session = AsyncMock()
        durable_session.commit.side_effect = lambda: call_order.append("durable_commit")
        metrics_session = AsyncMock()
        metrics_session.commit.side_effect = lambda: call_order.append("metrics_commit")
        durable_context = MagicMock()
        durable_context.__aenter__ = AsyncMock(return_value=durable_session)
        durable_context.__aexit__ = AsyncMock(return_value=None)
        metrics_context = MagicMock()
        metrics_context.__aenter__ = AsyncMock(return_value=metrics_session)
        metrics_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(side_effect=[durable_context, metrics_context])

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._redis_client = AsyncMock()
            consumer._redis_client.get_active_execution.return_value = {
                "workflow_id": "00000000-0000-0000-0000-000000000001",
                "workflow_name": "source_workflow",
                "org_id": "00000000-0000-0000-0000-000000000002",
                "user_id": "00000000-0000-0000-0000-000000000003",
                "user_email": "test@example.com",
                "user_name": "Test User",
                "sync": True,
                "event": {"id": "event-1", "type": "ticket.created"},
            }
            consumer._redis_client.push_result.side_effect = (
                lambda **_kwargs: call_order.append("push_result")
            )
            completed_event = AsyncMock(
                side_effect=lambda **_kwargs: call_order.append("completed_event")
            )
            stage_logs = AsyncMock(return_value=MagicMock(count=0))
            recover_logs = AsyncMock()

            with (
                patch(
                    "src.core.database.get_session_factory",
                    return_value=session_factory,
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.update_execution",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
                patch.object(
                    WorkflowExecutionConsumer,
                    "_read_persisted_completion_timing",
                    new=AsyncMock(
                        return_value=(
                            None,
                            datetime(2026, 10, 28, tzinfo=timezone.utc),
                        )
                    ),
                ),
                patch("bifrost._logging.stage_logs_to_postgres", new=stage_logs),
                patch("bifrost._logging.recover_terminal_execution_logs", new=recover_logs),
                patch(
                    "src.core.metrics.update_daily_metrics",
                    new=AsyncMock(
                        side_effect=lambda **_kwargs: call_order.append("daily_metrics")
                    ),
                ),
                patch(
                    "src.core.metrics.update_workflow_roi_daily",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.publish_execution_update",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.publish_history_update",
                    new_callable=AsyncMock,
                ),
                patch("src.core.cache.cleanup_execution_cache", new_callable=AsyncMock),
                patch(
                    "src.services.events.builtins.emit_workflow_completed_event",
                    new=completed_event,
                ),
                patch(
                    "src.services.events.processor.update_delivery_from_execution",
                    new_callable=AsyncMock,
                ),
            ):
                await consumer._process_success(
                    "00000000-0000-0000-0000-000000000004",
                    {
                        "success": True,
                        "status": "Success",
                        "result": {"secret": "not in the event"},
                        "duration_ms": 123,
                        "sync": True,
                    },
                )

        assert call_order.index("durable_commit") < call_order.index("push_result")
        assert call_order.index("push_result") < call_order.index("completed_event")
        assert call_order.index("completed_event") < call_order.index("daily_metrics")
        assert completed_event.await_args.kwargs["trigger_event"] == {
            "id": "event-1",
            "type": "ticket.created",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["CompletedWithErrors", "Failed"])
    async def test_non_successful_result_does_not_emit_workflow_completed(self, status):
        """Only the canonical Success terminal status emits workflow.completed."""
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
                "workflow_id": "00000000-0000-0000-0000-000000000001",
                "workflow_name": "source_workflow",
                "org_id": None,
                "user_id": None,
                "user_email": None,
                "user_name": "Bifrost",
                "sync": False,
                "event": None,
            }
            completed_event = AsyncMock()

            with (
                patch(
                    "src.core.database.get_session_factory",
                    return_value=session_factory,
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.update_execution",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
                patch(
                    "src.core.metrics.update_daily_metrics", new_callable=AsyncMock
                ),
                patch(
                    "src.core.metrics.update_workflow_roi_daily", new_callable=AsyncMock
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.publish_execution_update",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.publish_history_update",
                    new_callable=AsyncMock,
                ),
                patch("src.core.cache.cleanup_execution_cache", new_callable=AsyncMock),
                patch(
                    "src.services.events.builtins.emit_workflow_completed_event",
                    new=completed_event,
                ),
            ):
                await consumer._process_success(
                    "00000000-0000-0000-0000-000000000004",
                    {
                        "success": True,
                        "status": status,
                        "duration_ms": 123,
                        "sync": False,
                    },
                )

        completed_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fenced_success_does_not_emit_workflow_completed(self):
        """A stale executor result cannot create a completion event."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        session = AsyncMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=None)

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._redis_client = AsyncMock()
            consumer._redis_client.get_active_execution.return_value = {
                "workflow_id": "00000000-0000-0000-0000-000000000001",
                "workflow_name": "source_workflow",
                "org_id": None,
                "user_id": None,
                "user_email": None,
                "user_name": "Bifrost",
                "sync": False,
                "event": None,
            }
            completed_event = AsyncMock()

            with (
                patch(
                    "src.core.database.get_session_factory",
                    return_value=MagicMock(return_value=context),
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.update_execution",
                    new_callable=AsyncMock,
                    return_value=False,
                ),
                patch(
                    "src.services.events.builtins.emit_workflow_completed_event",
                    new=completed_event,
                ),
            ):
                await consumer._process_success(
                    "00000000-0000-0000-0000-000000000004",
                    {
                        "success": True,
                        "status": "Success",
                        "duration_ms": 123,
                        "sync": False,
                    },
                )

        session.rollback.assert_awaited_once()
        completed_event.assert_not_awaited()


class TestFailedExecutionCompletionOrder:
    """Failure responses follow the same durable-first ordering as successes."""

    @pytest.mark.asyncio
    async def test_sync_failure_is_pushed_before_metrics_and_terminal_fan_out(self):
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        call_order: list[str] = []
        durable_session = AsyncMock()
        durable_session.commit.side_effect = lambda: call_order.append(
            "durable_commit"
        )
        metrics_session = AsyncMock()
        metrics_session.commit.side_effect = lambda: call_order.append(
            "metrics_commit"
        )

        durable_context = MagicMock()
        durable_context.__aenter__ = AsyncMock(return_value=durable_session)
        durable_context.__aexit__ = AsyncMock(return_value=None)
        metrics_context = MagicMock()
        metrics_context.__aenter__ = AsyncMock(return_value=metrics_session)
        metrics_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(side_effect=[durable_context, metrics_context])

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._redis_client = AsyncMock()
            consumer._redis_client.get_active_execution.return_value = {
                "workflow_id": "00000000-0000-0000-0000-000000000001",
                "workflow_name": "failed_workflow",
                "org_id": "00000000-0000-0000-0000-000000000002",
                "user_id": "00000000-0000-0000-0000-000000000003",
                "user_email": "test@example.com",
                "user_name": "Test User",
                "sync": True,
            }
            consumer._redis_client.push_result.side_effect = (
                lambda **_kwargs: call_order.append("push_result")
            )

            update_delivery = AsyncMock()
            stage_logs = AsyncMock(return_value=MagicMock(count=0))
            recover_logs = AsyncMock()

            with (
                patch(
                    "src.core.database.get_session_factory",
                    return_value=session_factory,
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.update_execution",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.services.events.processor.update_delivery_from_execution",
                    new=update_delivery,
                ),
                patch(
                    "bifrost._logging.stage_logs_to_postgres",
                    new=stage_logs,
                ),
                patch("bifrost._logging.recover_terminal_execution_logs", new=recover_logs),
                patch(
                    "src.core.metrics.update_daily_metrics",
                    new=AsyncMock(
                        side_effect=lambda **_kwargs: call_order.append(
                            "daily_metrics"
                        )
                    ),
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.publish_execution_update",
                    new=AsyncMock(
                        side_effect=lambda *_args, **_kwargs: call_order.append(
                            "publish_execution"
                        )
                    ),
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.publish_history_update",
                    new=AsyncMock(
                        side_effect=lambda **_kwargs: call_order.append(
                            "publish_history"
                        )
                    ),
                ),
                patch(
                    "src.core.cache.cleanup_execution_cache",
                    new=AsyncMock(
                        side_effect=lambda _execution_id: call_order.append(
                            "cleanup_cache"
                        )
                    ),
                ),
                patch(
                    "src.services.events.builtins.emit_workflow_failure_events",
                    new_callable=AsyncMock,
                ),
            ):
                await consumer._process_failure(
                    "00000000-0000-0000-0000-000000000004",
                    {
                        "success": False,
                        "status": "Failed",
                        "error": "boom",
                        "error_type": "RuntimeError",
                        "duration_ms": 123,
                        "sync": True,
                    },
                )

        assert call_order[:2] == ["durable_commit", "push_result"]
        assert call_order.index("push_result") < call_order.index("daily_metrics")
        assert call_order.index("push_result") < call_order.index("metrics_commit")
        assert call_order.index("push_result") < call_order.index("publish_execution")
        assert call_order.index("push_result") < call_order.index("publish_history")
        assert call_order.index("push_result") < call_order.index("cleanup_cache")
        update_delivery.assert_not_awaited()
        stage_logs.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000004", durable_session
        )

    @pytest.mark.asyncio
    async def test_event_failure_updates_its_delivery(self):
        """The non-event fast path must not weaken event completion tracking."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        session = AsyncMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=None)
        metrics_context = MagicMock()
        metrics_context.__aenter__ = AsyncMock(return_value=AsyncMock())
        metrics_context.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(side_effect=[context, metrics_context])
        update_delivery = AsyncMock()
        receipt = MagicMock(count=1)
        stage_logs = AsyncMock(return_value=receipt)
        recover_logs = AsyncMock()

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._redis_client = AsyncMock()
            consumer._redis_client.get_active_execution.return_value = {
                "workflow_id": "00000000-0000-0000-0000-000000000001",
                "workflow_name": "event_workflow",
                "org_id": "00000000-0000-0000-0000-000000000002",
                "user_id": "00000000-0000-0000-0000-000000000003",
                "user_email": "test@example.com",
                "user_name": "Test User",
                "sync": False,
                "event": {"event_id": "event-1"},
            }

            with (
                patch(
                    "src.core.database.get_session_factory",
                    return_value=session_factory,
                ),
                patch(
                    "src.jobs.consumers.workflow_execution.update_execution",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.services.events.processor.update_delivery_from_execution",
                    new=update_delivery,
                ),
                patch(
                    "bifrost._logging.stage_logs_to_postgres",
                    new=stage_logs,
                ),
                patch("bifrost._logging.recover_terminal_execution_logs", new=recover_logs),
                patch(
                    "src.core.metrics.update_daily_metrics",
                    new_callable=AsyncMock,
                ),
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
                    "00000000-0000-0000-0000-000000000004",
                    {
                        "success": False,
                        "error": "boom",
                        "error_type": "RuntimeError",
                        "duration_ms": 123,
                        "sync": False,
                        "logs": [{"message": "captured failure log"}],
                    },
                )

        update_delivery.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000004",
            "Failed",
            error_message="boom",
            session=session,
        )
        stage_logs.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000004", session
        )
