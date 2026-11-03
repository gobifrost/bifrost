"""Run against the isolated candidate overlay, not an accepted upstream repair."""
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import AsyncMock, Mock, patch

from src.worker.remote.remote_pool import ThinRemoteProcessPool


class StartupCandidateTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = SimpleNamespace(connected=True, request=AsyncMock())
        self.pool = ThinRemoteProcessPool(self.transport, worker_id='synthetic-candidate')
        self.redis = SimpleNamespace(hset=AsyncMock(return_value=1), aclose=AsyncMock(), exists=AsyncMock(return_value=0))
        self.register = self.enterContext(patch.object(self.pool, '_register_worker', new_callable=AsyncMock))
        self.unregister = self.enterContext(patch.object(self.pool, '_unregister_worker', new_callable=AsyncMock))
        self.owner = self.enterContext(patch.object(self.pool, '_owns_session', new_callable=AsyncMock, return_value=True))
        self.memory = self.enterContext(patch.object(self.pool, '_read_remote_memory', Mock()))

        async def redis_client():
            self.pool._redis = self.redis
            return self.redis

        self.enterContext(patch.object(self.pool, '_get_redis', side_effect=redis_client))
        self.hold = asyncio.Event()

        async def idle():
            await self.hold.wait()

        for name in ('_heartbeat_loop', '_lease_refresh_loop', '_cancel_listener_loop', '_command_listener_loop'):
            self.enterContext(patch.object(self.pool, name, idle))

    async def asyncTearDown(self):
        for task in self.tasks():
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in self.tasks() if task is not None), return_exceptions=True)

    def tasks(self):
        return [self.pool._heartbeat_task, self.pool._lease_refresh_task, self.pool._cancel_task, self.pool._command_task]

    async def test_configure_failure_does_not_claim_started_or_clean_unowned_state(self):
        self.transport.request.side_effect = RuntimeError('configure')
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'configure'):
                await self.pool.start()
        self.assertFalse(self.pool._started)
        self.assertEqual(self.transport.request.await_count, 2)
        self.unregister.assert_not_awaited()

    async def test_registration_failure_rolls_back_then_retry_starts_listeners(self):
        self.register.side_effect = RuntimeError('register')
        with self.assertRaisesRegex(RuntimeError, 'register'):
            await self.pool.start()
        self.assertFalse(self.pool._started)
        self.unregister.assert_awaited_once()
        self.register.side_effect = None
        await self.pool.start()
        self.assertEqual(sum(task is not None for task in self.tasks()), 4)
        self.assertEqual(self.transport.request.await_count, 2)

    async def test_tagging_failure_closes_owned_client(self):
        self.redis.hset.side_effect = RuntimeError('tagging')
        with self.assertRaisesRegex(RuntimeError, 'tagging'):
            await self.pool.start()
        self.assertFalse(self.pool._started)
        self.assertIsNone(self.pool._redis)
        self.redis.aclose.assert_awaited_once()

    async def test_memory_failure_rolls_back(self):
        self.memory.side_effect = RuntimeError('memory')
        with self.assertRaisesRegex(RuntimeError, 'memory'):
            await self.pool.start()
        self.assertFalse(self.pool._started)
        self.register.assert_not_awaited()

    async def test_cancellation_during_registration_rolls_back(self):
        entered = asyncio.Event()

        async def register():
            entered.set()
            await self.hold.wait()

        self.register.side_effect = register
        task = asyncio.create_task(self.pool.start())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.pool._started)
        self.unregister.assert_awaited_once()

    async def test_concurrent_start_waits_for_initial_start(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def register():
            entered.set()
            await release.wait()

        self.register.side_effect = register
        first = asyncio.create_task(self.pool.start())
        await entered.wait()
        second = asyncio.create_task(self.pool.start())
        try:
            await asyncio.sleep(0)
            self.assertFalse(second.done())
        finally:
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(self.register.await_count, 1)

    async def test_failed_rollback_quarantines_start_and_retains_both_errors(self):
        original, cleanup = RuntimeError('register'), RuntimeError('cleanup')
        self.register.side_effect = original
        self.unregister.side_effect = cleanup
        with self.assertRaises(BaseExceptionGroup) as caught:
            await self.pool.start()
        self.assertEqual(caught.exception.exceptions, (original, cleanup))
        self.assertTrue(self.pool._shutdown)
        with self.assertRaisesRegex(RuntimeError, 'rollback incomplete'):
            await self.pool.start()
        self.assertEqual(self.transport.request.await_count, 1)
        self.unregister.side_effect = None
        await self.pool.stop()
        self.assertFalse(self.pool._started)

    async def test_superseded_owner_rollback_does_not_unregister_replacement(self):
        self.register.side_effect = RuntimeError('register')
        self.owner.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'register'):
            await self.pool.start()
        self.unregister.assert_not_awaited()
        self.assertFalse(self.pool._started)

    async def test_healthy_start_stop_and_repeat_start(self):
        await self.pool.start()
        await self.pool.start()
        self.assertEqual(self.transport.request.await_count, 1)
        tasks = self.tasks()
        await self.pool.stop()
        self.assertTrue(all(task is not None and task.done() for task in tasks))
        self.assertFalse(self.pool._started)
        self.assertIsNone(self.pool._redis)
        self.redis.aclose.assert_awaited_once()

    async def test_stop_waits_for_incomplete_start_then_settles_created_listeners(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def register():
            entered.set()
            await release.wait()

        self.register.side_effect = register
        first = asyncio.create_task(self.pool.start())
        await entered.wait()
        stop = asyncio.create_task(self.pool.stop())
        try:
            await asyncio.sleep(0)
            self.assertFalse(stop.done())
        finally:
            release.set()
            await asyncio.gather(first, stop)
        self.assertFalse(self.pool._started)
        self.assertTrue(all(task is not None and task.done() for task in self.tasks()))

    async def test_second_cancellation_quarantines_interrupted_rollback(self):
        entered, cleanup = asyncio.Event(), asyncio.Event()

        async def register():
            entered.set()
            await self.hold.wait()

        async def unregister():
            cleanup.set()
            await self.hold.wait()

        self.register.side_effect = register
        self.unregister.side_effect = unregister
        task = asyncio.create_task(self.pool.start())
        await entered.wait()
        task.cancel()
        await cleanup.wait()
        task.cancel()
        with self.assertRaises(BaseExceptionGroup) as caught:
            await task
        self.assertEqual(len(caught.exception.exceptions), 2)
        self.assertTrue(all(isinstance(error, asyncio.CancelledError) for error in caught.exception.exceptions))
        with self.assertRaisesRegex(RuntimeError, 'rollback incomplete'):
            await self.pool.start()
        self.unregister.side_effect = None
        await self.pool.stop()
        self.assertFalse(self.pool._started)

    async def test_swallowed_unregister_failure_cannot_advertise_clean_rollback(self):
        original = RuntimeError('register')
        self.register.side_effect = original
        # Upstream _unregister_worker logs and swallows Redis delete failures.
        self.redis.exists.return_value = 1
        with self.assertRaises(BaseExceptionGroup) as caught:
            await self.pool.start()
        self.assertIs(caught.exception.exceptions[0], original)
        self.assertTrue(self.pool._shutdown)
        with self.assertRaisesRegex(RuntimeError, 'rollback incomplete'):
            await self.pool.start()
        self.redis.exists.return_value = 0
        await self.pool.stop()
        self.assertFalse(self.pool._started)


if __name__ == '__main__':
    main()
