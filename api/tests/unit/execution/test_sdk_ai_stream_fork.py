"""Engine-local ``ai.stream`` over a real forked child.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child streams AI completions through the installed stream transport
while an ordinary unary ``config.get`` runs mid-stream, a second stream
reuses the channel, a third stream cancels early, and a fourth proves
the channel stays reusable. The child's fixed-operation HTTP route is
hard-disabled (dead ``BIFROST_API_URL``), so success proves zero fixed
AI API requests: every chunk arrived over the parent-local channel.

The parent serves the production ``STREAM_OPS`` registry (the real
``ai.stream`` source) with a fake LLM provider, so deltas, done
payloads, usage attribution, and the chunked large-file open all run
the production code path.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from src.services.execution.sdk_local_dispatch import principal_from_context
from src.services.execution.sdk_stream_dispatch import serve_stream_channel
from src.services.execution.template_process import TemplateProcess

pytestmark = pytest.mark.slow


@pytest_asyncio.fixture(autouse=True)
async def _fresh_shared_redis():
    """Rebind the shared async Redis singleton to each test's loop.

    pytest-asyncio runs every test on a fresh event loop, but
    ``src.core.redis_client``'s singleton persists across tests; reusing a
    connection minted on a previous test's loop raises "attached to a
    different loop". Dispose around each test so pumps mint connections on
    the current loop.
    """
    from src.core.redis_client import close_redis_client

    await close_redis_client()
    yield
    await close_redis_client()


@contextlib.asynccontextmanager
async def _factory(db_session):
    yield db_session


@contextlib.asynccontextmanager
async def _null_factory():
    yield None


def _context_for(function_name: str, file_path: str, execution_id: str) -> dict:
    return {
        "execution_id": execution_id,
        "name": function_name,
        "function_name": function_name,
        "file_path": file_path,
        "parameters": {},
        "caller": {
            "user_id": "fork-ai-stream-user",
            "email": "fork-ai-stream@test.local",
            "name": "Fork AI Stream Test",
        },
        "organization": None,
        "tags": [],
        "timeout_seconds": 120,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": False,
        # One-shot token; the child's HTTP route is dead by env design, so
        # any HTTP attempt fails loudly instead of succeeding silently.
        "engine_token": "fork-ai-stream-dead-token",
    }


def _entry_source(function_name: str, key: str, org_id: str) -> str:
    return (
        "from bifrost import workflow\n"
        "\n"
        "\n"
        f'@workflow(name="{function_name}", description="ai-stream-local fork test")\n'
        f"async def {function_name}():\n"
        "    import os\n"
        "    from bifrost import config\n"
        "    from bifrost.ai import ai as ai_facade\n"
        "    from bifrost.models import AIInputFile\n"
        "    _probe = AIInputFile(\n"
        '        filename="blob.bin",\n'
        '        content_type="application/octet-stream",\n'
        "        data=b\"x\" * 100000,\n"
        "    )\n"
        f"    _stream = ai_facade.stream('hello', files=[_probe], org_id={org_id!r})\n"
        "    _first = (await _stream.__anext__()).model_dump()\n"
        f"    _value = await config.get({key!r})\n"
        "    _rest = []\n"
        "    async for _chunk in _stream:\n"
        "        _rest.append(_chunk.model_dump())\n"
        "        if _chunk.done:\n"
        "            break\n"
        f"    _second = [c.model_dump() async for c in ai_facade.stream('again', org_id={org_id!r})]\n"
        f"    _cancel = ai_facade.stream('cancel-me', org_id={org_id!r})\n"
        "    _cancelled_first = (await _cancel.__anext__()).model_dump()\n"
        "    await _cancel.aclose()\n"
        f"    _fourth = [c.model_dump() async for c in ai_facade.stream('after-cancel', org_id={org_id!r})]\n"
        "    return {\n"
        "        'first': _first,\n"
        "        'rest': _rest,\n"
        "        'value': _value,\n"
        "        'second': _second,\n"
        "        'cancelled_first': _cancelled_first,\n"
        "        'fourth': _fourth,\n"
        "        'had_db_url': (\n"
        "            'BIFROST_DATABASE_URL' in os.environ\n"
        "            or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
        "        ),\n"
        "    }\n"
    )


async def _seed_s3_only(relpath: str, content: str) -> None:
    """Write a workspace file to S3 without warming the Redis cache."""
    from src.services.repo_storage import RepoStorage

    await RepoStorage().write(relpath, content.encode("utf-8"))


async def _drop_s3(relpath: str) -> None:
    from src.services.repo_storage import RepoStorage

    with contextlib.suppress(Exception):
        await RepoStorage().delete(relpath)


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


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


class _ScriptedClient:
    """Fake provider serving one script per ``stream()`` call."""

    def __init__(self, scripts):
        self.provider_name = "openai"
        self.model_name = "gpt-4o"
        self.scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []
        self.tracked: list[_TrackedStream] = []

    def stream(self, *, messages=None, max_tokens=None, model=None):
        self.calls.append(
            {"messages": messages, "max_tokens": max_tokens, "model": model}
        )
        script = self.scripts[min(len(self.calls) - 1, len(self.scripts) - 1)]
        tracked = _TrackedStream(_run_script(script))
        self.tracked.append(tracked)
        return tracked


async def _run_script(script):
    for item in script:
        if item == "hang":
            await asyncio.sleep(3600)
        else:
            yield item


@pytest.mark.asyncio
class TestForkedAIStream:
    async def test_ai_stream_local_end_to_end(self, db_session, monkeypatch):
        """A real child streams AI locally with HTTP disabled."""
        from shared.sdk_config import set_sdk_config_value

        tag = uuid4().hex[:8]
        key = f"fork-ai-stream-{tag}"
        await set_sdk_config_value(
            db_session,
            key=key,
            value="stream-value",
            is_secret=False,
            org_id=None,
            actor_email="ai-stream-fork-test",
        )
        org_id = str(uuid4())
        execution_id = str(uuid4())
        function_name = f"fork_ai_stream_{tag}"
        entry_path = f"workflows/fork_ai_stream_{tag}.py"
        await _seed_s3_only(entry_path, _entry_source(function_name, key, org_id))
        # Hard-disable HTTP for every forked child of this test: any SDK
        # call that reaches HTTP fails with connection-refused, so success
        # proves the local transports served every operation.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")
        # The stream source resolves its own short session through the
        # real factory hook; point it at this test's session.
        monkeypatch.setattr(
            "src.core.database.get_session_factory",
            lambda: (lambda: _factory(db_session)),
        )

        client = _ScriptedClient(
            [
                [_delta("a"), _delta("b"), _done(3, 5)],
                [_delta("z"), _done(1, 2)],
                [_delta("q"), "hang"],
                [_done(7, 8)],
            ]
        )
        usage_records: list[dict[str, Any]] = []

        async def _record_usage(**kwargs):
            usage_records.append(kwargs)

        import src.services.llm as llm_pkg

        template = TemplateProcess()
        template.start()
        pumps: list = []
        conns = []
        try:
            (
                child_pid,
                work_queue,
                result_queue,
                sdk_req,
                sdk_resp,
                imp_req,
                imp_resp,
                stream_req,
                stream_resp,
            ) = template.fork(
                worker_id="sdk-ai-stream-fork",
                with_sdk=True,
                with_import=True,
                with_stream=True,
            )
            conns = [sdk_req, sdk_resp, imp_req, imp_resp, stream_req, stream_resp]
            context = _context_for(function_name, entry_path, execution_id)
            principal = principal_from_context(context)

            from src.services.execution.sdk_local_dispatch import (
                IMPORT_CHANNEL_ALLOWED_OPS,
                SDK_CHANNEL_ALLOWED_OPS,
                serve_channel,
            )

            sdk_pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=lambda: _factory(db_session),
                    principal=principal,
                    allowed_ops=SDK_CHANNEL_ALLOWED_OPS,
                )
            )
            imp_pump = asyncio.create_task(
                serve_channel(
                    recv_conn=imp_req,
                    send_conn=imp_resp,
                    session_factory=lambda: _null_factory(),
                    principal=principal,
                    allowed_ops=IMPORT_CHANNEL_ALLOWED_OPS,
                )
            )
            stream_pump = asyncio.create_task(
                serve_stream_channel(
                    recv_conn=stream_req,
                    send_conn=stream_resp,
                    principal=principal,
                )
            )
            pumps = [sdk_pump, imp_pump, stream_pump]

            with (
                patch.object(
                    llm_pkg, "get_llm_client", new=AsyncMock(return_value=client)
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
                work_queue.put(("exec-fork-ai-stream", context))
                envelope = await asyncio.to_thread(result_queue.get, True, 120.0)

            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["had_db_url"] is False
            # First stream: incremental first chunk, unary config mid-stream,
            # then the rest plus the done payload with tokens.
            assert result["first"] == {
                "content": "a",
                "done": False,
                "input_tokens": None,
                "output_tokens": None,
            }
            assert result["value"] == "stream-value"
            assert result["rest"] == [
                {
                    "content": "b",
                    "done": False,
                    "input_tokens": None,
                    "output_tokens": None,
                },
                {"content": "", "done": True, "input_tokens": 3, "output_tokens": 5},
            ]
            # Second stream reuses the same channel.
            assert result["second"] == [
                {
                    "content": "z",
                    "done": False,
                    "input_tokens": None,
                    "output_tokens": None,
                },
                {"content": "", "done": True, "input_tokens": 1, "output_tokens": 2},
            ]
            # Early cancel delivered one chunk; the fourth stream still
            # completes on the same channel.
            assert result["cancelled_first"] == {
                "content": "q",
                "done": False,
                "input_tokens": None,
                "output_tokens": None,
            }
            assert result["fourth"] == [
                {"content": "", "done": True, "input_tokens": 7, "output_tokens": 8}
            ]

            # The large input file rode the chunked open frames end to end.
            assert len(client.calls) == 4
            first_messages = client.calls[0]["messages"]
            file_inputs = first_messages[-1].input_files
            assert len(file_inputs) == 1
            assert file_inputs[0].data == b"x" * 100000
            # The cancelled provider stream was closed by the parent.
            assert client.tracked[2].closed is True
            # Three completed streams recorded usage under the parent
            # execution id and requested org; the cancelled one did not.
            assert len(usage_records) == 3
            for record in usage_records:
                assert record["execution_id"] == UUID(execution_id)
                assert record["organization_id"] == UUID(org_id)
                assert record["provider"] == "openai"

            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(sdk_pump, timeout=15.0) == "eof"
            assert await asyncio.wait_for(imp_pump, timeout=15.0) == "eof"
            assert await asyncio.wait_for(stream_pump, timeout=15.0) == "eof"
            pumps = []
        finally:
            for pump in pumps:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()
            await _drop_s3(entry_path)
