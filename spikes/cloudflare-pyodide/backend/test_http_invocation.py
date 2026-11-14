"""HTTP/framing isolation only; actual engine/SDK proof is separate."""
import asyncio
import json
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import patch
from uuid import uuid4

import httpx

from http_invocation import HttpInvocation

CLIENT = httpx.AsyncClient


def envelope():
    run = str(uuid4())
    return {'mode': 'canonical', 'execution_id': run, 'context': {'execution_id': run, 'engine_token': 'synthetic-secret'}}


def response(request, *, cookies=False):
    body = json.loads(request.content)
    run = body['execution_id']
    handle = 'invocation:' + run
    frames = [{'protocol': 1, 'type': 'accepted', 'execution_id': run, 'runtime_handle': handle},
              {'protocol': 1, 'type': 'result', 'execution_id': run, 'runtime_handle': handle,
               'evidence': {'runtime_handle': handle, 'result': {'execution_id': run, 'success': True}}}]
    headers = {'Content-Type': 'application/x-ndjson'}
    if cookies:
        headers['Set-Cookie'] = 'per-run=synthetic-secret; Path=/'
    return httpx.Response(200, headers=headers, content=''.join(json.dumps(row) + '\n' for row in frames))


class InvocationTests(IsolatedAsyncioTestCase):
    async def test_snapshot_is_single_use_and_repr_does_not_expose_credentials(self):
        body = envelope()
        original = body['execution_id']
        accepted, sent = [], []
        headers = {'X-Dev-Auth': 'synthetic-secret'}
        invocation = HttpInvocation('https://worker.invalid/', body, accepted.append, headers=headers)
        headers['X-Dev-Auth'] = 'mutated'
        body['context']['engine_token'] = 'mutated'
        body['execution_id'] = str(uuid4())

        def handle(request):
            self.assertEqual(request.headers['x-dev-auth'], 'synthetic-secret')
            sent.append(json.loads(request.content))
            return response(request)

        with patch('http_invocation.httpx.AsyncClient', side_effect=lambda **kw: CLIENT(transport=httpx.MockTransport(handle), **kw)):
            evidence = await invocation.collect()
            with self.assertRaisesRegex(RuntimeError, 'single-use'):
                await invocation.collect()
        self.assertEqual(sent[0]['execution_id'], original)
        self.assertEqual(sent[0]['context']['engine_token'], 'synthetic-secret')
        self.assertEqual(accepted, [evidence['runtime_handle']])
        self.assertEqual(len(sent), 1)
        self.assertNotIn('synthetic-secret', repr(invocation))
        with self.assertRaises(AttributeError):
            invocation.execution_id = 'replacement'

    async def test_concurrent_attempts_have_distinct_frames_and_no_shared_cookie_jar(self):
        sent, accepted = [], [[], []]

        async def handle(request):
            await asyncio.sleep(0)
            sent.append(request)
            return response(request, cookies=True)

        invocations = [HttpInvocation('https://worker.invalid/', envelope(), sink.append) for sink in accepted]
        with patch('http_invocation.httpx.AsyncClient', side_effect=lambda **kw: CLIENT(transport=httpx.MockTransport(handle), **kw)):
            results = await asyncio.gather(*(item.collect() for item in invocations))
            await HttpInvocation('https://worker.invalid/', envelope(), accepted[0].append).collect()
        self.assertNotEqual(results[0]['runtime_handle'], results[1]['runtime_handle'])
        self.assertEqual(accepted[1], [results[1]['runtime_handle']])
        self.assertTrue(all('cookie' not in request.headers for request in sent))

    async def test_redirect_is_not_followed(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(307, headers={'Location': 'https://other.invalid/'})

        invocation = HttpInvocation('https://worker.invalid/', envelope(), lambda handle: None)
        with patch('http_invocation.httpx.AsyncClient', side_effect=lambda **kw: CLIENT(transport=httpx.MockTransport(handle), **kw)):
            with self.assertRaises(ConnectionError):
                await invocation.collect()
        self.assertEqual(len(requests), 1)

    async def test_invalid_identity_bounds_endpoint_and_cookies_rejected_before_network(self):
        body = envelope()
        for target in ['http://other.invalid/', 'https://user:synthetic-secret@worker.invalid/', 'https://worker.invalid/?secret=x']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                HttpInvocation(target, body, lambda handle: None)
        with self.assertRaises(ValueError):
            HttpInvocation('https://worker.invalid/', {**body, 'execution_id': str(uuid4())}, lambda handle: None)
        with self.assertRaises(ValueError):
            HttpInvocation('https://worker.invalid/', {**body, 'padding': 'x' * 262144}, lambda handle: None)
        with self.assertRaises(ValueError):
            HttpInvocation('https://worker.invalid/', body, lambda handle: None, headers={'Cookie': 'secret=x'})
        with self.assertRaises(ValueError):
            HttpInvocation('https://worker.invalid/', body, lambda handle: None, headers={'accept': 'text/plain'})


if __name__ == '__main__':
    main()
