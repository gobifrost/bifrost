"""Controlled receipt interleavings; no platform or physical-stop claim."""
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from attempt_journal import AttemptJournal


class DeliveryTests(IsolatedAsyncioTestCase):
    async def test_stalled_ack_does_not_block_other_dispatch_or_delivery(self):
        lease = SimpleNamespace(worker_id=uuid4(), launcher_id=uuid4())
        journal = AttemptJournal(capacity=2)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def result():
            return {'success': True}

        async def accept(route, _execution, _result):
            calls.append(route)
            if route == 'a':
                entered.set()
                await release.wait()
            return True

        with patch('attempt_journal.owns_remote_session', new_callable=AsyncMock, return_value=True):
            await (await journal.dispatch(lease, 'a', 'a', result))
            first = asyncio.create_task(journal.reconcile_terminal(lease, 'a', accept))
            await entered.wait()
            dispatch = asyncio.create_task(journal.dispatch(lease, 'b', 'b', result))
            duplicate = asyncio.create_task(journal.reconcile_terminal(lease, 'a', accept))
            second = None
            try:
                await asyncio.sleep(0)
                self.assertTrue(dispatch.done(), 'another route dispatch waits for stalled ACK')
                await (await dispatch)
                second = asyncio.create_task(journal.reconcile_terminal(lease, 'b', accept))
                await asyncio.sleep(0)
                self.assertTrue(second.done(), 'another route delivery waits for stalled ACK')
                self.assertTrue(second.result())
                self.assertFalse(duplicate.done())
                with self.assertRaisesRegex(RuntimeError, 'unacknowledged'):
                    await journal.retire()
            finally:
                release.set()
                await first
                await duplicate
                await (await dispatch)
                if second is not None:
                    await second
            self.assertEqual(calls.count('a'), 1)
            await journal.reconcile_terminal(lease, 'b', accept)
            await journal.retire()


if __name__ == '__main__':
    main()
