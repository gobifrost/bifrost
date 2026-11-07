"""Fault ownership/framing only; real engine/SDK fixture is separate."""
import asyncio
import json
from unittest import IsolatedAsyncioTestCase, main

import httpx

from retained_http_fault import RetainedHttpFault


class Stream(httpx.AsyncByteStream):
    def __init__(self, finish):
        self.finish = finish
        self.closed = False

    async def __aiter__(self):
        accepted = {'protocol': 1, 'type': 'accepted', 'execution_id': 'run', 'runtime_handle': 'actual-handle'}
        yield (json.dumps(accepted) + '\n').encode()
        await self.finish.wait()
        yield (json.dumps({**accepted, 'type': 'result', 'evidence': {'runtime_handle': 'actual-handle',
            'result': {'execution_id': 'run', 'success': True}}}) + '\n').encode()

    async def aclose(self):
        self.closed = True


class FaultTests(IsolatedAsyncioTestCase):
    async def test_original_client_close_leaves_retained_stream_owned_until_eof(self):
        finish = asyncio.Event()
        stream = Stream(finish)
        fault = RetainedHttpFault('run', lambda: httpx.MockTransport(
            lambda _request: httpx.Response(200, headers={'Content-Type': 'application/x-ndjson'}, stream=stream)))
        try:
            async with fault.client() as client:
                with self.assertRaises(httpx.ReadError):
                    await client.post('http://bifrost-pyodide-ddf316c1:8799/', json={'execution_id': 'run'})
            self.assertFalse(stream.closed)
            if fault.task is None:
                raise RuntimeError('Retained task missing')
            self.assertFalse(fault.task.done())
            finish.set()
            result = await fault.wait()
            self.assertTrue(result['result']['success'])
            self.assertTrue(stream.closed)
            self.assertEqual(fault.posts, 1)
            async with fault.client() as client:
                with self.assertRaisesRegex(RuntimeError, 'second physical'):
                    await client.post('http://bifrost-pyodide-ddf316c1:8799/', json={'execution_id': 'run'})
        finally:
            await fault.close()


if __name__ == '__main__':
    main()
