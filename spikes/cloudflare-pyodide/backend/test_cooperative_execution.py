import asyncio
from unittest import IsolatedAsyncioTestCase

from cooperative_execution import execute_cooperatively, request_cancel, active_execution_count


class CooperativeTests(IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        self.assertEqual(active_execution_count(), 0)

    async def test_owner_scheduled_cancel_awaits_cleanup(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def execute():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        pending = asyncio.create_task(execute_cooperatively('run', execute, owner_scheduled=True))
        await entered.wait()
        self.assertTrue(request_cancel('run'))
        self.assertTrue(request_cancel('run'))
        result = await pending
        self.assertTrue(result['cooperative_task_cancelled'])
        self.assertTrue(cleaned.is_set())

    async def test_owner_scheduled_suppression_can_await_again(self):
        entered = asyncio.Event()

        async def execute():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0)
                return {'status': 'Success', 'result': 'suppressed'}

        pending = asyncio.create_task(execute_cooperatively('run', execute, owner_scheduled=True))
        await entered.wait()
        self.assertTrue(request_cancel('run'))
        self.assertEqual(await pending, {'status': 'Success', 'result': 'suppressed'})

    async def test_normal_result_is_unchanged_and_late_cancel_refused(self):
        result = {'status': 'Success', 'result': 7}

        async def execute():
            return result

        self.assertIs(await execute_cooperatively('run', execute), result)
        self.assertFalse(request_cancel('run'))

    async def test_matching_cancel_is_idempotent_and_awaits_coroutine_cleanup(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def execute():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        pending = asyncio.create_task(execute_cooperatively('run', execute))
        await entered.wait()
        self.assertFalse(request_cancel('other'))
        self.assertTrue(request_cancel('run'))
        self.assertTrue(request_cancel('run'))
        result = await pending
        self.assertTrue(cleaned.is_set())
        self.assertEqual(result, {'cooperative_task_cancelled': True,
                                 'physical_stopped': False, 'execution_settled': False})

    async def test_suppressed_cancel_preserves_actual_success(self):
        entered = asyncio.Event()
        result = {'status': 'Success', 'result': 'suppressed'}

        async def execute():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return result

        pending = asyncio.create_task(execute_cooperatively('run', execute))
        await entered.wait()
        request_cancel('run')
        self.assertIs(await pending, result)

    async def test_user_raised_cancel_is_not_an_owner_cancellation_receipt(self):
        async def execute():
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await execute_cooperatively('run', execute)

    async def test_cancelled_waiter_is_not_a_requested_execution_cancel(self):
        entered = asyncio.Event()

        async def execute():
            entered.set()
            await asyncio.Event().wait()

        pending = asyncio.create_task(execute_cooperatively('run', execute))
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending

    async def test_cleanup_failure_is_preserved_after_requested_cancel(self):
        entered = asyncio.Event()

        async def execute():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                raise RuntimeError('synthetic cleanup failure')

        pending = asyncio.create_task(execute_cooperatively('run', execute))
        await entered.wait()
        self.assertTrue(request_cancel('run'))
        with self.assertRaisesRegex(RuntimeError, 'synthetic cleanup failure'):
            await pending

    async def test_second_execution_is_refused_before_calling_user_code(self):
        entered = asyncio.Event()
        second_called = False

        async def execute():
            entered.set()
            await asyncio.Event().wait()

        async def second():
            nonlocal second_called
            second_called = True

        pending = asyncio.create_task(execute_cooperatively('run', execute))
        try:
            await entered.wait()
            with self.assertRaisesRegex(RuntimeError, 'Fresh single-execution'):
                await execute_cooperatively('second', second)
            self.assertFalse(second_called)
            self.assertEqual(active_execution_count(), 1)
        finally:
            request_cancel('run')
            await pending

    async def test_main_task_cancellation_does_not_prove_spawned_task_stopped(self):
        entered, release, effect = asyncio.Event(), asyncio.Event(), asyncio.Event()
        background = None

        async def untracked():
            await release.wait()
            effect.set()

        async def execute():
            nonlocal background
            background = asyncio.create_task(untracked())
            entered.set()
            await asyncio.Event().wait()

        pending = asyncio.create_task(execute_cooperatively('run', execute))
        try:
            await entered.wait()
            self.assertTrue(request_cancel('run'))
            outcome = await pending
            self.assertTrue(outcome['cooperative_task_cancelled'])
            self.assertFalse(outcome['execution_settled'])
            self.assertNotIn('status', outcome)
            self.assertFalse(background.done())
            release.set()
            await background
            self.assertTrue(effect.is_set())
        finally:
            if background is not None and not background.done():
                background.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await background
