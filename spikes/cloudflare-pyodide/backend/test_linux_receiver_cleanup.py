"""Real asyncio receiver lifecycle tests; no SDK, provider or database invocation."""
import asyncio
from pathlib import Path
import runpy
import unittest

cancel_receiver = runpy.run_path(str(Path(__file__).with_name('linux-control-reference.py')))['cancel_control_receiver']


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
        await cancel_receiver(task)
        self.assertTrue(task.cancelled())
        self.assertTrue(cleaned.is_set())

    async def test_already_completed_receiver_is_joined(self):
        async def receive():
            return None

        task = asyncio.create_task(receive())
        await task
        await cancel_receiver(task)
        self.assertTrue(task.done())
        self.assertFalse(task.cancelled())

    async def test_receiver_failure_is_not_suppressed(self):
        async def receive():
            raise ValueError('fixed receiver failure')

        task = asyncio.create_task(receive())
        await asyncio.sleep(0)
        with self.assertRaisesRegex(ValueError, '^fixed receiver failure$'):
            await cancel_receiver(task)


if __name__ == '__main__':
    unittest.main()
