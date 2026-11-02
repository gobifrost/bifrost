"""Route-principal guards with live-session validation doubled; no database/provider."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import attempt_journal
from src.worker.remote.ownership import RemoteOwnerLease


class RouteOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lease = RemoteOwnerLease(uuid4(), uuid4(), uuid4(), 1, 2)
        # Both callers pass the session check. Route ownership is a separate guard.
        self.session = patch.object(attempt_journal, 'owns_remote_session', AsyncMock(return_value=True))
        self.session.start()
        self.addCleanup(self.session.stop)
        self.journal = attempt_journal.AttemptJournal()
        self.launch = AsyncMock(return_value={'execution_id': 'execution', 'success': True})
        self.receipt = await self.journal.dispatch(self.lease, 'route', 'execution', self.launch)
        await self.receipt

    async def test_another_live_worker_cannot_retrieve_route_result(self):
        other = replace(self.lease, worker_id=uuid4())
        with self.assertRaisesRegex(ValueError, 'route owner conflict'):
            await self.journal.dispatch(other, 'route', 'execution', self.launch)
        self.launch.assert_awaited_once()

    async def test_another_launcher_cannot_retrieve_route_result(self):
        other = replace(self.lease, launcher_id=uuid4())
        with self.assertRaisesRegex(ValueError, 'route owner conflict'):
            await self.journal.dispatch(other, 'route', 'execution', self.launch)
        self.launch.assert_awaited_once()

    async def test_other_owner_cannot_consume_or_acknowledge_terminal(self):
        other = replace(self.lease, worker_id=uuid4())
        accept = AsyncMock(return_value=True)
        with self.assertRaisesRegex(ValueError, 'route owner conflict'):
            await self.journal.reconcile_terminal(other, 'route', accept)
        accept.assert_not_awaited()
        self.assertFalse(self.journal.inventory()[0]['acknowledged'])

    async def test_acknowledged_tombstone_still_rejects_other_owner(self):
        accepted = await self.journal.reconcile_terminal(self.lease, 'route', AsyncMock(return_value=True))
        self.assertTrue(accepted)
        with self.assertRaisesRegex(ValueError, 'route owner conflict'):
            await self.journal.reconcile_terminal(replace(self.lease, worker_id=uuid4()), 'route', AsyncMock())

    async def test_retained_result_cannot_be_consumed_by_other_worker(self):
        failed = AsyncMock(side_effect=ConnectionError('synthetic response loss'))
        receipt = await self.journal.dispatch(self.lease, 'failed-route', 'failed-execution', failed)
        with self.assertRaises(ConnectionError):
            await receipt
        other = replace(self.lease, worker_id=uuid4())
        consumer = SimpleNamespace(owner_worker_id=other.worker_id, _handle_result=AsyncMock())
        execution = SimpleNamespace(execution_id='failed-execution', attach_transport_metadata=lambda value: value)
        event = {'event': 'result', 'route_id': 'failed-route', '_event_id': 'retained-event',
                 'result': {'execution_id': 'failed-execution', 'success': True}}
        with self.assertRaisesRegex(ValueError, 'route owner conflict'):
            await self.journal.reconcile_retained_workflow(other, 'failed-route', event, consumer, execution)
        consumer._handle_result.assert_not_awaited()
        failed.assert_awaited_once()

    async def test_same_launcher_reconnect_can_retrieve_and_acknowledge_once(self):
        replacement = replace(self.lease, owner_id=uuid4(), session_generation=2)
        receipt = await self.journal.dispatch(replacement, 'route', 'execution', self.launch)
        self.assertIs(receipt, self.receipt)
        accept = AsyncMock(return_value=True)
        accepted = await self.journal.reconcile_terminal(replacement, 'route', accept)
        replay = await self.journal.reconcile_terminal(replacement, 'route', accept)
        self.assertTrue(accepted)
        self.assertTrue(replay)
        accept.assert_awaited_once()
        self.launch.assert_awaited_once()
        await self.journal.retire()
        self.assertEqual(self.journal.inventory(), [])


if __name__ == '__main__':
    unittest.main()
