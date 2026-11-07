"""Task ownership only; real SDK close is qualified separately under workerd."""
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from sdk_cleanup import close_sdk, _pending


class CleanupTests(IsolatedAsyncioTestCase):
    async def test_normal_close_is_awaited_once(self):
        client = SimpleNamespace(close=AsyncMock())
        await close_sdk(client)
        client.close.assert_awaited_once()
        self.assertFalse(_pending)

    async def test_canceled_waiter_leaves_owned_close_running(self):
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def close():
            entered.set()
            await release.wait()
            finished.set()

        waiter = asyncio.create_task(close_sdk(SimpleNamespace(close=close)))
        await entered.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(len(_pending), 1)
        self.assertFalse(finished.is_set())
        release.set()
        await asyncio.gather(*tuple(_pending))
        self.assertTrue(finished.is_set())
        self.assertFalse(_pending)

    async def test_close_failure_remains_the_callers_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'Synthetic close failure'):
            await close_sdk(SimpleNamespace(close=AsyncMock(side_effect=RuntimeError('Synthetic close failure'))))
        self.assertFalse(_pending)
