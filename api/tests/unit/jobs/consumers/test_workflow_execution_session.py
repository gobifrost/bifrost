"""Tests for WorkflowExecutionConsumer session management.

Validates that the consumer uses short-lived sessions (no persistent session).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestConsumerSessionLifecycle:
    """Test that consumer no longer holds a persistent DB session."""

    @pytest.mark.asyncio
    async def test_start_does_not_create_persistent_session(self):
        """Consumer.start() should NOT create a persistent DB session."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._pool = AsyncMock()
            consumer._pool.start = AsyncMock()
            consumer._pool_started = False

            with patch.object(
                WorkflowExecutionConsumer.__bases__[0], "start", AsyncMock()
            ):
                await consumer.start()

            # No _db_session attribute should exist
            assert not hasattr(consumer, "_db_session")

    @pytest.mark.asyncio
    async def test_stop_does_not_close_session(self):
        """Consumer.stop() should not try to close a persistent session."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._pool = AsyncMock()
            consumer._pool.stop = AsyncMock()
            consumer._pool_started = True

            with patch.object(
                WorkflowExecutionConsumer.__bases__[0], "stop", AsyncMock()
            ):
                # Should complete without error
                await consumer.stop()

    @pytest.mark.asyncio
    async def test_no_get_db_session_method(self):
        """Consumer should not have _get_db_session() method (removed)."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        assert not hasattr(WorkflowExecutionConsumer, "_get_db_session")


class TestConsumerStartupOrder:
    """Pool must be fully started before RabbitMQ begins delivering messages.

    Regression test for a production incident where two worker pods leaked
    ~800 MB each: the consumer was accepting messages from RabbitMQ before
    ProcessPoolManager.start() had finished initializing its template
    process, and a now-removed spawn fallback created ghost worker
    processes that were never reaped.
    """

    @pytest.mark.asyncio
    async def test_pool_starts_before_rabbitmq_consumer(self):
        """Consumer.start() must call pool.start() before super().start().

        super().start() (BaseConsumer) is what calls queue.consume() and
        begins message delivery. If pool.start() hasn't completed by the
        time that happens, messages can be routed to a not-yet-ready pool.
        """
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        call_order: list[str] = []

        async def mock_pool_start() -> None:
            call_order.append("pool")

        async def mock_super_start(self) -> None:  # type: ignore[no-untyped-def]
            call_order.append("rabbitmq")

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._pool = MagicMock()
            consumer._pool.start = mock_pool_start
            consumer._pool_started = False

            with patch.object(
                WorkflowExecutionConsumer.__bases__[0], "start", mock_super_start
            ):
                await consumer.start()

        assert call_order == ["pool", "rabbitmq"], (
            f"Pool must start before RabbitMQ consumer; got {call_order}"
        )
        assert consumer._pool_started is True


class TestSuccessfulExecutionCompletionOrder:
    """Sync callers should not wait for non-critical completion fan-out."""

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
        session = AsyncMock()
        session.commit.side_effect = lambda: call_order.append("commit")
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._redis_client = AsyncMock()
            consumer._redis_client.get_pending_execution.return_value = {
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
            consumer._redis_client.delete_pending_execution.side_effect = (
                lambda _execution_id: call_order.append("delete_pending")
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

            with (
                patch(
                    "src.core.database.get_session_factory",
                    return_value=session_factory,
                ),
                patch(
                    "src.repositories.executions.update_execution",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.services.events.processor.update_delivery_from_execution",
                    new_callable=AsyncMock,
                ),
                patch(
                    "bifrost._sync.flush_pending_changes",
                    new_callable=AsyncMock,
                    return_value=0,
                ),
                patch(
                    "bifrost._logging.flush_logs_to_postgres",
                    new_callable=AsyncMock,
                    return_value=0,
                ),
                patch(
                    "src.core.metrics.update_daily_metrics",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.core.metrics.update_workflow_roi_daily",
                    new_callable=AsyncMock,
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
                    },
                )

        assert call_order[:2] == ["commit", "push_result"]
        assert call_order.index("push_result") < call_order.index("publish_execution")
        assert call_order.index("push_result") < call_order.index("publish_history")
        assert call_order.index("push_result") < call_order.index("cleanup_cache")
        assert call_order.index("push_result") < call_order.index("delete_pending")

        terminal_data = publish_execution.await_args.args[2]
        assert terminal_data == {"duration_ms": 123}
