"""Single physical HTTP invocation; no scheduling, authorization or retry authority."""
import json
from uuid import UUID

import httpx

from invocation_stream import InvocationStream


class HttpInvocation:
    """Immutable request snapshot and isolated HTTP session for one native attempt.

    The trusted caller supplies an already-authorized canonical envelope and
    handles route deduplication/fencing through upstream. This object performs
    at most one POST, retains parsed terminal evidence on lost EOF, and never
    changes business-job state. It is not a public provider admission API.
    """
    def __init__(self, endpoint, envelope, on_accepted, *, headers=None):
        url = httpx.URL(endpoint)
        local = url.scheme == 'http' and url.host == 'bifrost-pyodide-ddf316c1' and url.port == 8799
        if ((url.scheme != 'https' and not local) or not url.host or url.username or url.password
            or url.query or url.fragment):
            raise ValueError('Trusted HTTPS or fixed isolated-test endpoint required')
        if not isinstance(envelope, dict):
            raise ValueError('Canonical invocation identity required')
        execution_id = envelope.get('execution_id')
        try:
            valid_id = isinstance(execution_id, str) and str(UUID(execution_id)) == execution_id
        except ValueError:
            valid_id = False
        context = envelope.get('context')
        if (not valid_id or envelope.get('mode') != 'canonical' or not isinstance(context, dict)
            or context.get('execution_id') != execution_id):
            raise ValueError('Canonical invocation identity required')
        body = json.dumps(envelope, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(body) > 262144:
            raise ValueError('Invocation request exceeds bound')
        self._execution_id = execution_id
        self._endpoint = str(url)
        self._body = body
        self._headers = dict(headers or {})
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in self._headers.items()):
            raise ValueError('Native invocation headers must be strings')
        reserved = {'cookie', 'accept', 'content-type', 'content-length', 'transfer-encoding', 'host', 'connection'}
        if any(key.lower() in reserved for key in self._headers):
            raise ValueError('Invocation framing and session headers are reserved')
        self._headers.update({'Accept': 'application/x-ndjson', 'Content-Type': 'application/json'})
        self._reader = InvocationStream(execution_id, on_accepted)
        self._started = False

    def __repr__(self):
        return f'HttpInvocation(execution_id={self.execution_id!r})'

    @property
    def execution_id(self):
        return self._execution_id

    @property
    def runtime_handle(self):
        return self._reader.runtime_handle

    @property
    def terminal(self):
        return self._reader.terminal

    async def collect(self):
        if self._started:
            raise RuntimeError('Invocation transport is single-use; reconcile the original attempt')
        self._started = True
        # Own the client through EOF. Never share a cookie jar or follow a
        # provider redirect carrying scoped credentials to another endpoint.
        async with httpx.AsyncClient(timeout=90, follow_redirects=False, trust_env=False) as client:
            async with client.stream('POST', self._endpoint, content=self._body, headers=self._headers) as response:
                return await self._reader.collect(response)
