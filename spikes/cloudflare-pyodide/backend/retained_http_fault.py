"""Isolated fixture: retain the real response but lose its ACK to the caller."""
import asyncio
import json

import httpx

from invocation_stream import InvocationStream


class _LostAck(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b''
        raise httpx.ReadError('Injected response loss before ACK')


class RetainedHttpFault:
    def __init__(self, execution_id, transport_factory):
        self.execution_id = execution_id
        self.transport_factory = transport_factory
        self.task: asyncio.Task[dict] | None = None
        self.reader: InvocationStream | None = None
        self.posts = 0

    def client(self, **options):
        return httpx.AsyncClient(transport=_RetainingTransport(self, self.transport_factory()), **options)

    async def wait(self):
        if self.task is None:
            raise RuntimeError('No retained invocation')
        return await asyncio.shield(self.task)

    async def close(self):
        # Failure cleanup only; cancellation here never establishes child stop.
        if self.task is not None:
            if not self.task.done():
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


class _RetainingTransport(httpx.AsyncBaseTransport):
    def __init__(self, owner, inner):
        self.owner, self.inner, self.retaining = owner, inner, False

    async def handle_async_request(self, request):
        target = (request.method == 'POST' and request.url.host == 'bifrost-pyodide-ddf316c1'
                  and request.url.port == 8799 and request.url.path == '/'
                  and json.loads(request.content).get('execution_id') == self.owner.execution_id)
        if target and self.owner.task is not None:
            raise RuntimeError('Fixture forbids a second physical invocation')
        response = await self.inner.handle_async_request(request)
        if not target:
            return response
        self.owner.posts += 1
        self.retaining = True
        accepted = asyncio.Event()
        self.owner.reader = InvocationStream(self.owner.execution_id, lambda _handle: accepted.set())

        async def retain():
            try:
                return await self.owner.reader.collect(response)
            finally:
                await response.aclose()
                await self.inner.aclose()

        self.owner.task = asyncio.create_task(retain())
        ready = asyncio.create_task(accepted.wait())
        try:
            await asyncio.wait((ready, self.owner.task), return_when=asyncio.FIRST_COMPLETED)
            if not accepted.is_set():
                await self.owner.task
                raise ConnectionError('No actual provider acceptance')
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)
        return httpx.Response(200, headers={'Content-Type': 'application/x-ndjson'}, stream=_LostAck())

    async def aclose(self):
        if not self.retaining:
            await self.inner.aclose()
