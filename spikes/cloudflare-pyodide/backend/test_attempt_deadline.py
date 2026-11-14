"""Adapter boundaries; SDK/canonical compatibility is tested in the real guest."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from attempt_deadline import report_expired_workflow, supervise_attempt_deadline
from src.services.execution.process_pool import ExecutionInfo
from src.worker.remote.protocol import CredentialRouteChanged


class DeadlineTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.lease = SimpleNamespace(worker_id=uuid4())
        self.consumer = SimpleNamespace(owner_worker_id=self.lease.worker_id, _handle_result=AsyncMock())
        self.execution = ExecutionInfo(execution_id='run-1',
            started_at=datetime.now(timezone.utc) - timedelta(seconds=31), timeout_seconds=30,
            active_execution={'execution_id': 'run-1', 'sync': True})

    async def test_expired_delivery_reuses_parent_identity_and_leaves_execution_info_intact(self):
        original = (self.execution.started_at, self.execution.timeout_seconds)
        with patch('attempt_deadline.owns_remote_session', AsyncMock(return_value=True)):
            await report_expired_workflow(self.lease, self.consumer, self.execution)
        result = self.consumer._handle_result.await_args.args[0]
        self.assertEqual(result['execution_id'], 'run-1')
        self.assertEqual(result['error_type'], 'TimeoutError')
        self.assertTrue(result['sync'])
        self.assertNotIn('metrics', result)
        self.assertEqual((self.execution.started_at, self.execution.timeout_seconds), original)

    async def test_early_deadline_rejected_before_callback(self):
        self.execution.started_at = datetime.now(timezone.utc)
        with patch('attempt_deadline.owns_remote_session', AsyncMock(return_value=True)):
            with self.assertRaisesRegex(ValueError, 'not expired'):
                await report_expired_workflow(self.lease, self.consumer, self.execution)
        self.consumer._handle_result.assert_not_awaited()

    async def test_stale_owner_rejected_before_callback(self):
        with patch('attempt_deadline.owns_remote_session', AsyncMock(return_value=False)):
            with self.assertRaises(CredentialRouteChanged):
                await report_expired_workflow(self.lease, self.consumer, self.execution)
        self.consumer._handle_result.assert_not_awaited()

    async def test_callback_error_propagates_without_claiming_consumption(self):
        self.consumer._handle_result.side_effect = ConnectionError('fixed lost receipt')
        with patch('attempt_deadline.owns_remote_session', AsyncMock(return_value=True)):
            with self.assertRaises(ConnectionError):
                await report_expired_workflow(self.lease, self.consumer, self.execution)

    async def test_owner_change_after_callback_remains_uncertain(self):
        with patch('attempt_deadline.owns_remote_session', AsyncMock(side_effect=[True, False])):
            with self.assertRaises(CredentialRouteChanged):
                await report_expired_workflow(self.lease, self.consumer, self.execution)
        self.consumer._handle_result.assert_awaited_once()

    async def test_supervisor_leaves_successful_collection_to_normal_result_path(self):
        self.execution.started_at = datetime.now(timezone.utc)
        collection = asyncio.get_running_loop().create_future()
        collection.set_result({'success': True})
        with patch('attempt_deadline.report_expired_workflow', AsyncMock()) as report:
            self.assertFalse(await supervise_attempt_deadline(self.lease, self.consumer, self.execution, collection))
        report.assert_not_awaited()

    async def test_supervisor_reports_expired_failed_collection_without_replacing_it(self):
        collection = asyncio.get_running_loop().create_future()
        collection.set_exception(ConnectionError('fixed response loss'))
        with patch('attempt_deadline.report_expired_workflow', AsyncMock()) as report:
            self.assertTrue(await supervise_attempt_deadline(self.lease, self.consumer, self.execution, collection))
        report.assert_awaited_once_with(self.lease, self.consumer, self.execution)
        self.assertIsInstance(collection.exception(), ConnectionError)

    async def test_cutoff_uses_current_owner_without_changing_original_deadline(self):
        current = SimpleNamespace(worker_id=self.lease.worker_id, session_generation=2)
        collection = asyncio.get_running_loop().create_future()
        original = self.execution.started_at
        with patch('attempt_deadline.report_expired_workflow', AsyncMock()) as report:
            self.assertTrue(await supervise_attempt_deadline(self.lease, self.consumer, self.execution,
                collection, lease_at_cutoff=lambda: current))
        report.assert_awaited_once_with(current, self.consumer, self.execution)
        self.assertEqual(self.execution.started_at, original)
        self.assertFalse(collection.done())
        collection.set_result(None)

    async def test_supervisor_cancellation_does_not_cancel_live_collection(self):
        self.execution.started_at = datetime.now(timezone.utc)
        collection = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(supervise_attempt_deadline(self.lease, self.consumer, self.execution, collection))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(collection.done())
        collection.set_result(None)  # Settle only this test-owned future.

    async def test_expired_deadline_is_not_hidden_by_delayed_timer_task_start(self):
        self.execution.started_at = datetime.now(timezone.utc) - timedelta(seconds=29.99)
        collection = asyncio.get_running_loop().create_future()
        collection.set_result({'success': True})
        # A short real scheduler stall occurs after duration calculation but
        # before the timer task starts. No wall/monotonic clocks are patched.
        asyncio.get_running_loop().call_soon(time.sleep, 0.02)
        with patch('attempt_deadline.report_expired_workflow', AsyncMock()) as report:
            self.assertTrue(await supervise_attempt_deadline(self.lease, self.consumer, self.execution, collection))
        report.assert_awaited_once()

    async def test_supervisor_rejects_unretained_coroutine_before_starting_work(self):
        async def unretained():
            return None

        coroutine = unretained()
        try:
            with self.assertRaisesRegex(TypeError, 'Task/Future required'):
                await supervise_attempt_deadline(self.lease, self.consumer, self.execution, coroutine)
        finally:
            coroutine.close()
        self.consumer._handle_result.assert_not_awaited()


if __name__ == '__main__':
    main()
