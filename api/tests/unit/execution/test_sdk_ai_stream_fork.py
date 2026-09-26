"""Engine-local ``ai.stream`` over a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child is forked with the worker's private Unix socket injected exactly
as the pool does, then runs ``ai.stream`` through the full ``bifrost.ai``
facade. The test process serves the **real** ``/api/sdk/ai/stream`` SSE
route on that socket via uvicorn against the worker's global database
engine, with the child's network API dead and the legacy stream channel
absent. Success proves the migrated streaming call reached the
parent-served route, not the old custom channel and not a network call.

The provider is faked in the parent (no paid external API); no real key is
used. The fake is keyed by the last user message so the same script serves
both the socket-served child stream and a direct call to the route function
the external network API exposes; the test asserts the child's public
``AIStreamChunk`` payloads match that route's SSE events, so the socket and
external-network paths cannot drift.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID as _UUID
from uuid import uuid4

import pytest

from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

pytestmark = pytest.mark.slow


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str, engine_token: str, execution_id: str) -> dict:
    return {
        "execution_id": execution_id,
        "name": "sdk-ai-stream-fork-test",
        "code": code_b64,
        "parameters": {},
        "caller": {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "email": "engine@bifrost.internal",
            "name": "Bifrost Engine",
        },
        "organization": None,
        "tags": [],
        "timeout_seconds": 120,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": True,
        "engine_token": engine_token,
    }


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


async def _wait_until(predicate, timeout: float = 5.0) -> bool:
    """Wait for an out-of-band server side effect; never masks a failure."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def _run_socket_fork(server: WorkerSdkHttpServer, context: dict) -> dict:
    """Fork one child against the worker socket and return its envelope."""
    template = TemplateProcess()
    template.start()
    try:
        child_pid, work_queue, result_queue = template.fork(
            worker_id="sdk-ai-stream-fork",
            sdk_socket_path=server.socket_path,
        )
        try:
            work_queue.put((context["execution_id"], context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
        finally:
            work_queue.close()
            result_queue.close()
        _wait_for_pid_to_die(child_pid)
        return envelope
    finally:
        template.shutdown()


_AI_SOURCE = (
    "import os, sys\n"
    "from bifrost.ai import ai as ai_facade\n"
    "from bifrost.client import get_engine_socket_path\n"
    "from bifrost._stream_transport import get as _get_stream_transport\n"
    "from bifrost.models import AIInputFile\n"
    "_used_socket = get_engine_socket_path() is not None\n"
    "_used_channel = _get_stream_transport() is not None\n"
    "_probe = AIInputFile(\n"
    "    filename='blob.bin',\n"
    "    content_type='application/octet-stream',\n"
    "    data=b'x' * 100000,\n"
    ")\n"
    "_first_stream = ai_facade.stream('parity', files=[_probe], org_id=ORG_ID)\n"
    "_first = (await _first_stream.__anext__()).model_dump()\n"
    "_rest = []\n"
    "async for _chunk in _first_stream:\n"
    "    _rest.append(_chunk.model_dump())\n"
    "    if _chunk.done:\n"
    "        break\n"
    "_second = [\n"
    "    c.model_dump() async for c in ai_facade.stream('again', org_id=ORG_ID)\n"
    "]\n"
    "_cancel = ai_facade.stream('cancel-me', org_id=ORG_ID)\n"
    "_cancelled_first = (await _cancel.__anext__()).model_dump()\n"
    "await _cancel.aclose()\n"
    "_fourth = [\n"
    "    c.model_dump()\n"
    "    async for c in ai_facade.stream('after-cancel', org_id=ORG_ID)\n"
    "]\n"
    "_error_chunks = [\n"
    "    c.model_dump() async for c in ai_facade.stream('fail-me', org_id=ORG_ID)\n"
    "]\n"
    "result = {\n"
    "    'used_socket': _used_socket,\n"
    "    'used_channel': _used_channel,\n"
    "    'first': _first,\n"
    "    'rest': _rest,\n"
    "    'second': _second,\n"
    "    'cancelled_first': _cancelled_first,\n"
    "    'fourth': _fourth,\n"
    "    'error_chunks': _error_chunks,\n"
    "    'had_db_url': (\n"
    "        'BIFROST_DATABASE_URL' in os.environ\n"
    "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
    "    ),\n"
    "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
    "}\n"
)


def _delta(content):
    return SimpleNamespace(type="delta", content=content)


def _done(input_tokens=3, output_tokens=5):
    return SimpleNamespace(
        type="done",
        content=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
        cache_write_tokens=0,
        provider_cost=None,
    )


def _provider_error(message):
    return SimpleNamespace(type="error", error=message)


class _TrackedStream:
    def __init__(self, gen):
        self._gen = gen
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._gen.__anext__()

    async def aclose(self):
        self.closed = True
        await self._gen.aclose()


async def _run_script(script):
    for item in script:
        if item == "hang":
            await asyncio.sleep(3600)
        else:
            yield item


class _ProviderClient:
    """Fake provider serving a script keyed by the last user message."""

    def __init__(self, scripts):
        self.provider_name = "openai"
        self.model_name = "gpt-4o"
        self.scripts = scripts
        self.calls: list[dict[str, Any]] = []
        self.tracked: list[_TrackedStream] = []

    def stream(self, *, messages=None, max_tokens=None, model=None):
        prompt = messages[-1].content
        self.calls.append(
            {
                "prompt": prompt,
                "messages": messages,
                "max_tokens": max_tokens,
                "model": model,
            }
        )
        tracked = _TrackedStream(_run_script(self.scripts[prompt]))
        self.tracked.append(tracked)
        return tracked


def _chunks_to_payloads(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map public ``AIStreamChunk`` dumps back to the SSE payload shape."""
    payloads: list[dict[str, Any]] = []
    for chunk in chunks:
        if chunk["done"]:
            payloads.append(
                {
                    "done": True,
                    "input_tokens": chunk["input_tokens"],
                    "output_tokens": chunk["output_tokens"],
                }
            )
        else:
            payloads.append({"content": chunk["content"]})
    return payloads


async def _call_stream_route(async_session_factory, prompt: str) -> list[dict]:
    """Invoke the external HTTP route function directly and read its SSE."""
    from src.models.contracts.cli import CLIAICompleteRequest
    from src.routers.cli import cli_ai_stream

    async with async_session_factory() as session:
        response = await cli_ai_stream(
            CLIAICompleteRequest(messages=[{"role": "user", "content": prompt}]),
            SimpleNamespace(
                user_id=uuid4(),
                organization_id=None,
                is_superuser=True,
            ),
            session,
        )
        lines: list[str] = []
        async for raw in response.body_iterator:
            lines.extend(line for line in raw.splitlines() if line)
    return [
        json.loads(line[6:])
        for line in lines
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


@pytest.mark.asyncio
class TestForkedAIStream:
    async def test_ai_stream_over_socket_with_network_dead(
        self, async_session_factory, monkeypatch
    ):
        """A real child streams AI over the socket with HTTP disabled."""
        from src.core.security import mint_engine_token

        execution_id = str(uuid4())
        org_id = str(uuid4())
        engine_token, _ = mint_engine_token(
            execution_id=execution_id,
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=120,
        )
        context = _context_for(
            _script_b64(_AI_SOURCE.replace("ORG_ID", repr(org_id))),
            engine_token,
            execution_id,
        )

        provider = _ProviderClient(
            {
                "parity": [_delta("a"), _delta("b"), _done(3, 5)],
                "again": [_delta("z"), _done(1, 2)],
                "cancel-me": [_delta("q"), "hang"],
                "after-cancel": [_delta("w"), _done(7, 8)],
                "fail-me": [_delta("x"), _provider_error("boom")],
            }
        )
        usage_records: list[dict[str, Any]] = []

        async def _record_usage(**kwargs):
            usage_records.append(kwargs)

        import src.services.llm as llm_pkg

        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        server = WorkerSdkHttpServer()
        await server.start()
        assert server.socket_path is not None
        parity_events: list[dict] = []
        try:
            with (
                patch.object(
                    llm_pkg,
                    "get_llm_client",
                    new=AsyncMock(return_value=provider),
                ),
                patch(
                    "src.core.cache.get_shared_redis",
                    new=AsyncMock(return_value=AsyncMock()),
                ),
                patch(
                    "src.services.ai_usage_service.record_ai_usage",
                    new=_record_usage,
                ),
            ):
                envelope = await _run_socket_fork(server, context)
                assert envelope["success"] is True, envelope
                assert await _wait_until(lambda: len(usage_records) == 3)
                # External parity: the same route function the network API
                # exposes, consumed directly, yields the same SSE payloads.
                parity_events = await _call_stream_route(
                    async_session_factory, "parity"
                )
        finally:
            await server.stop()

        assert envelope["success"] is True, envelope
        result = envelope["result"]
        # The fixed-operation call rode the socket; the legacy channel is
        # absent, and the child holds no DB credential.
        assert result["used_socket"] is True, result
        assert result["used_channel"] is False, result
        assert result["had_db_url"] is False, result
        assert result["had_sqlalchemy"] is False, result

        # Chunks and done: incremental first chunk, then the rest.
        assert result["first"] == {
            "content": "a",
            "done": False,
            "input_tokens": None,
            "output_tokens": None,
        }
        assert result["rest"] == [
            {
                "content": "b",
                "done": False,
                "input_tokens": None,
                "output_tokens": None,
            },
            {"content": "", "done": True, "input_tokens": 3, "output_tokens": 5},
        ]
        # External parity: the socket-served public chunks equal the route's
        # SSE payloads for the identical script.
        assert _chunks_to_payloads([result["first"], *result["rest"]]) == parity_events
        assert parity_events == [
            {"content": "a"},
            {"content": "b"},
            {"done": True, "input_tokens": 3, "output_tokens": 5},
        ]

        # A second full stream works after the first drained.
        assert result["second"] == [
            {
                "content": "z",
                "done": False,
                "input_tokens": None,
                "output_tokens": None,
            },
            {"content": "", "done": True, "input_tokens": 1, "output_tokens": 2},
        ]

        # Early close delivered one chunk and closed the provider stream; a
        # later stream still completes.
        assert result["cancelled_first"] == {
            "content": "q",
            "done": False,
            "input_tokens": None,
            "output_tokens": None,
        }
        assert await _wait_until(lambda: provider.tracked[2].closed)
        assert result["fourth"] == [
            {"content": "w", "done": False, "input_tokens": None, "output_tokens": None},
            {"content": "", "done": True, "input_tokens": 7, "output_tokens": 8},
        ]

        # Provider-error parity: one empty non-done chunk, then EOF.
        assert result["error_chunks"] == [
            {"content": "x", "done": False, "input_tokens": None, "output_tokens": None},
            {"content": "", "done": False, "input_tokens": None, "output_tokens": None},
        ]

        # The large input file rode the request body end to end.
        parity_calls = [call for call in provider.calls if call["prompt"] == "parity"]
        file_inputs = parity_calls[0]["messages"][-1].input_files
        assert len(file_inputs) == 1
        assert file_inputs[0].data == b"x" * 100000

        # Completed streams recorded usage under the requested org; the
        # cancelled and provider-error streams did not. The direct route call
        # (external parity) has no execution/org scope.
        assert len(usage_records) == 4
        assert all(record["provider"] == "openai" for record in usage_records)
        assert [record["organization_id"] for record in usage_records[:3]] == [
            _UUID(org_id)
        ] * 3
        assert [record["execution_id"] for record in usage_records[:3]] == [
            _UUID(execution_id)
        ] * 3
        assert usage_records[3]["organization_id"] is None
        assert usage_records[3]["execution_id"] is None
