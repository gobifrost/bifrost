"""Real asyncio receiver lifecycle tests; no SDK, provider or database invocation."""
import asyncio
import unittest
from task_cleanup import cancel_and_join


class ReceiverCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_waits_for_receiver_finally(self):
        entered = asyncio.Event()
        cleaned = asyncio.Event()

        async def receive():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        task = asyncio.create_task(receive())
        await entered.wait()
        await cancel_and_join(task)
        self.assertTrue(task.cancelled())
        self.assertTrue(cleaned.is_set())

    async def test_already_completed_receiver_is_joined(self):
        async def receive():
            return 'receiver completed'

        task = asyncio.create_task(receive())
        self.assertEqual(await task, 'receiver completed')
        await cancel_and_join(task)
        self.assertTrue(task.done())
        self.assertFalse(task.cancelled())
        self.assertEqual(task.result(), 'receiver completed')

    async def test_receiver_failure_is_not_suppressed(self):
        async def receive():
            raise ValueError('fixed receiver failure')

        task = asyncio.create_task(receive())
        await asyncio.sleep(0)
        with self.assertRaisesRegex(ValueError, '^fixed receiver failure$'):
            await cancel_and_join(task)

    async def test_parent_cancellation_is_not_suppressed(self):
        entered = asyncio.Event()
        cleaning = asyncio.Event()

        async def receive():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(receive())
        await entered.wait()
        join = asyncio.create_task(cancel_and_join(task))
        await cleaning.wait()
        join.cancel()
        outcome, = await asyncio.gather(join, return_exceptions=True)
        self.assertIsInstance(outcome, asyncio.CancelledError)
        self.assertTrue(join.cancelled())
        self.assertTrue(task.done())


if __name__ == '__main__':
    unittest.main()
