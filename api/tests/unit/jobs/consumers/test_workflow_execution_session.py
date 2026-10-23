"""Tests for WorkflowExecutionConsumer session management.

Validates that the consumer uses short-lived sessions (no persistent session).
"""

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
            consumer._redis_client.push_result.side_effect = (
                lambda **_kwargs: call_order.append("push_result")
            )

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
            flush_logs = AsyncMock(return_value=0)

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
                patch(
                    "src.services.events.processor.update_delivery_from_execution",
                    new=update_delivery,
                ),
                patch(
                    "bifrost._logging.flush_logs_to_postgres",
                    new=flush_logs,
                ),
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
            ):
                await consumer._process_success(
                    "00000000-0000-0000-0000-000000000004",
                    {
                        "success": True,
                        "status": "Success",
                        "result": {"rows": [{"value": "x" * 1000}]},
                        "duration_ms": 123,
                        "sync": True,
                    },
                )

        assert call_order[:2] == ["durable_commit", "push_result"]
        assert call_order.index("push_result") < call_order.index("daily_metrics")
        assert call_order.index("push_result") < call_order.index("workflow_metrics")
        assert call_order.index("push_result") < call_order.index("metrics_commit")
        assert call_order.index("push_result") < call_order.index("publish_execution")
        assert call_order.index("push_result") < call_order.index("publish_history")
        assert call_order.index("push_result") < call_order.index("cleanup_cache")
        update_delivery.assert_not_awaited()
        flush_logs.assert_not_awaited()

        terminal_data = publish_execution.await_args.args[2]
        assert terminal_data == {"duration_ms": 123}


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
            flush_logs = AsyncMock(return_value=0)

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
                    "bifrost._logging.flush_logs_to_postgres",
                    new=flush_logs,
                ),
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
        flush_logs.assert_not_awaited()

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
        flush_logs = AsyncMock(return_value=1)

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
                    "bifrost._logging.flush_logs_to_postgres",
                    new=flush_logs,
                ),
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
        flush_logs.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000004",
            session=session,
        )
