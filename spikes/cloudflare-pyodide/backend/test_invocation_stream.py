"""Framing only; real engine/SDK acceptance requires the guest integration run."""
import json
import unittest

from invocation_stream import InvocationStream


def frame(kind, **fields):
    return json.dumps(dict(protocol=1, type=kind, execution_id='run-1', runtime_handle='child-1', **fields)).encode()


class Response:
    status_code = 200
    headers = {'content-type': 'application/x-ndjson'}

    def __init__(self, chunks):
        self.chunks = chunks

    async def aiter_bytes(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_identity_bound_result(self):
        accepted = []
        reader = InvocationStream('run-1', accepted.append)
        evidence = {'runtime_handle': 'child-1', 'result': {'execution_id': 'run-1'}}
        wire = frame('accepted') + b'\n' + frame('result', evidence=evidence) + b'\n'
        self.assertEqual(await reader.collect(Response([wire[:11], wire[11:]])), evidence)
        self.assertEqual(accepted, ['child-1'])

    async def test_disconnect_retains_collected_terminal_without_certifying_eof(self):
        reader = InvocationStream('run-1', lambda handle: None)
        evidence = {'runtime_handle': 'child-1', 'result': {'execution_id': 'run-1'}}
        with self.assertRaises(ConnectionError):
            await reader.collect(Response([frame('accepted') + b'\n',
                frame('result', evidence=evidence) + b'\n', ConnectionError('lost EOF')]))
        self.assertEqual(reader.terminal, evidence)

    async def test_truncation_oversize_extra_frame_and_provider_error_are_uncertain(self):
        for chunks in ([frame('accepted') + b'\n'], [b'x' * 262145],
                       [frame('accepted') + b'\n' + frame('accepted') + b'\n'],
                       [frame('accepted') + b'\n' + frame('error', error_type='TimeoutError') + b'\n']):
            with self.subTest(chunks_length=sum(map(len, chunks))):
                with self.assertRaises(ConnectionError):
                    await InvocationStream('run-1', lambda handle: None).collect(Response(chunks))

    async def test_wrong_run_and_terminal_identity_rejected(self):
        for evidence in ({'runtime_handle': 'other', 'result': {'execution_id': 'run-1'}},
                         {'runtime_handle': 'child-1', 'result': {'execution_id': 'other'}}):
            reader = InvocationStream('run-1', lambda handle: None)
            reader.feed(frame('accepted'))
            with self.assertRaises(ConnectionError):
                reader.feed(frame('result', evidence=evidence))
        with self.assertRaises(ConnectionError):
            InvocationStream('other', lambda handle: None).feed(frame('accepted'))


if __name__ == '__main__':
    unittest.main()
