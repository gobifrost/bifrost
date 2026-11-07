"""Bounded accepted/result transport; no retry, dispatch queue or terminal authority."""
import json
import re


class InvocationStream:
    def __init__(self, execution_id, on_accepted):
        self.execution_id = execution_id
        self.on_accepted = on_accepted
        self.runtime_handle = None
        self.terminal = None
        self.phase = 0

    def feed(self, line):
        try:
            if len(line) > 262144:
                raise ValueError('Frame bound')
            frame = json.loads(line)
            if (not isinstance(frame, dict) or type(frame.get('protocol')) is not int or frame['protocol'] != 1
                or frame.get('execution_id') != self.execution_id):
                raise ValueError('Frame identity')
            handle = frame.get('runtime_handle')
            if not isinstance(handle, str) or re.fullmatch(r'[A-Za-z0-9._:-]{1,255}', handle) is None:
                raise ValueError('Frame handle')
            if self.phase == 0 and frame.get('type') == 'accepted':
                self.runtime_handle = handle
                self.phase = 1
                self.on_accepted(handle)
            elif self.phase == 1 and handle == self.runtime_handle and frame.get('type') == 'result':
                evidence = frame.get('evidence')
                if (not isinstance(evidence, dict) or evidence.get('runtime_handle') != handle
                    or not isinstance(evidence.get('result'), dict)
                    or evidence['result'].get('execution_id') != self.execution_id):
                    raise ValueError('Terminal identity')
                self.terminal = evidence
                self.phase = 2
            else:
                raise ValueError('Unexpected or failed frame')
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise ConnectionError('Invocation stream invalid or failed; reconciliation required') from exc

    async def collect(self, response):
        if response.status_code != 200 or response.headers.get('content-type', '').split(';')[0] != 'application/x-ndjson':
            raise ConnectionError('Invocation stream response unavailable')
        pending = b''
        # Do not request HTTPX fixed-size chunks: buffering a small accepted
        # frame until a full chunk arrives would delay dispatch acknowledgement.
        async for chunk in response.aiter_bytes():
            for offset in range(0, len(chunk), 16384):
                pending += chunk[offset:offset + 16384]
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    self.feed(line)
                if len(pending) > 262144:
                    raise ConnectionError('Invocation stream frame exceeded bound')
        if pending or self.phase != 2:
            raise ConnectionError('Invocation stream ended uncertainly; reconciliation required')
        return self.terminal
