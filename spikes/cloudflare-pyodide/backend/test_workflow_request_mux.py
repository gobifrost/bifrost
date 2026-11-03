"""Native request interleavings; these are not deployed isolation proof."""
import asyncio
from unittest import IsolatedAsyncioTestCase, main

from workflow_request_mux import WorkflowRequestMux


def payload(run):
    return {'execution_id': run, 'route_id': 'workflow:' + run, 'context': {'engine_token': run + '-secret'}}


class MuxTests(IsolatedAsyncioTestCase):
    async def test_two_routes_out_of_order_and_duplicate_request_do_not_mix_results(self):
        mux = WorkflowRequestMux(lambda: 7)
        release = {run: asyncio.Event() for run in ('a', 'b')}
        launches = []

        async def collect(request, accepted):
            run = request['execution_id']
            launches.append(run)
            accepted('handle:' + run)
            await release[run].wait()
            return {'execution_id': run, 'token_seen': request['context']['engine_token']}

        for run in release:
            mux.bind(run, collect)
        first = payload('a')
        a, b, duplicate = await asyncio.gather(
            mux.request('workflow', first, expected_generation=7),
            mux.request('workflow', payload('b'), expected_generation=7),
            mux.request('workflow', payload('a'), expected_generation=7))
        self.assertEqual(a, duplicate)
        self.assertNotEqual(a, b)
        first['context']['engine_token'] = 'replacement'
        release['b'].set()
        self.assertEqual((await mux.collection('b'))['token_seen'], 'b-secret')
        self.assertFalse(mux.collection('a').done())
        self.assertIsNone(mux.result('a'))
        release['a'].set()
        self.assertEqual((await mux.collection('a'))['token_seen'], 'a-secret')
        self.assertCountEqual(launches, ['a', 'b'])

    async def test_cancelled_request_waiter_does_not_cancel_physical_collection(self):
        mux = WorkflowRequestMux(lambda: 1)
        started, release = asyncio.Event(), asyncio.Event()

        async def collect(request, accepted):
            started.set()
            await release.wait()
            accepted('handle:a')
            return {'execution_id': request['execution_id']}

        mux.bind('a', collect)
        waiter = asyncio.create_task(mux.request('workflow', payload('a'), expected_generation=1))
        await started.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(mux.collection('a').cancelled())
        release.set()
        self.assertEqual((await mux.collection('a'))['execution_id'], 'a')
        self.assertEqual(await mux.request('workflow', payload('a'), expected_generation=1), {'runtime_handle': 'handle:a'})

    async def test_generation_and_changed_duplicate_rejected_before_another_effect(self):
        generation = [1]
        mux = WorkflowRequestMux(lambda: generation[0])
        calls = []

        async def collect(request, accepted):
            calls.append(request)
            accepted('handle:a')
            return {'execution_id': 'a'}

        mux.bind('a', collect)
        await mux.request('workflow', payload('a'), expected_generation=1)
        await mux.collection('a')
        changed = payload('a')
        changed['context']['engine_token'] = 'replacement'
        with self.assertRaises(ConnectionError):
            await mux.request('workflow', changed, expected_generation=1)
        generation[0] = 2
        with self.assertRaises(ConnectionError):
            await mux.request('workflow', payload('a'), expected_generation=1)
        with self.assertRaises(ConnectionError):
            await mux.request('workflow', payload('a'), expected_generation=2)
        with self.assertRaises(ConnectionError):
            await mux.request('terminate', payload('a'), expected_generation=2)
        with self.assertRaises(RuntimeError):
            mux.bind('a', collect)
        self.assertEqual(len(calls), 1)

    async def test_failed_collection_does_not_complete_peer_or_redispatch(self):
        mux = WorkflowRequestMux(lambda: 1)
        calls = []

        async def collect(request, accepted):
            run = request['execution_id']
            calls.append(run)
            if run == 'a':
                raise ConnectionError('synthetic stream loss')
            accepted('handle:b')
            return {'execution_id': run}

        mux.bind('a', collect)
        mux.bind('b', collect)
        for _ in range(2):
            with self.assertRaises(ConnectionError):
                await mux.request('workflow', payload('a'), expected_generation=1)
        self.assertEqual(await mux.request('workflow', payload('b'), expected_generation=1), {'runtime_handle': 'handle:b'})
        self.assertEqual((await mux.collection('b'))['execution_id'], 'b')
        self.assertIsNone(mux.result('a'))
        self.assertCountEqual(calls, ['a', 'b'])

    async def test_owner_rotation_before_collector_starts_refuses_effects(self):
        generation = [1]
        mux = WorkflowRequestMux(lambda: generation[0])
        calls = []

        async def collect(request, accepted):
            calls.append(request)
            accepted('handle:a')
            return {'execution_id': 'a'}

        mux.bind('a', collect)
        request = asyncio.create_task(mux.request('workflow', payload('a'), expected_generation=1))
        # FIFO scheduling: request admits and creates collection, then this
        # already-ready callback rotates ownership before collection begins.
        asyncio.get_running_loop().call_soon(generation.__setitem__, 0, 2)
        with self.assertRaises(ConnectionError):
            await request
        with self.assertRaisesRegex(ConnectionError, 'owner changed before collection'):
            await mux.collection('a')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    main()
