"""Stage 3b: real forked children served by the knowledge dispatcher.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the engine-local transport at engine start and runs an
inline script through the normal execution path, while the parent serves
all seven ``knowledge`` ops from a real database session with a stub
embedder. The child's HTTP route is hard-disabled (dead
``BIFROST_API_URL``) and its environment carries no database credentials,
so envelope success proves the local transport — zero API requests for
the fixed calls.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

import asyncio
import base64
import contextlib
import os
import time
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.execution.sdk_local_dispatch import (
    LocalDispatchPrincipal,
    serve_channel,
)
from src.services.execution.template_process import TemplateProcess

pytestmark = pytest.mark.slow


class _FakeEmbedder:
    def __init__(self, dim: int = 8):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [float((len(t) + i) % self.dim) for i in range(self.dim)] for t in texts
        ]

    async def embed_single(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str) -> dict:
    return {
        "execution_id": f"knowledge-fork-{uuid4().hex[:8]}",
        "name": "sdk-knowledge-local-fork-test",
        "code": code_b64,
        "parameters": {},
        "caller": {
            "user_id": "fork-test-user",
            "email": "fork@test.local",
            "name": "Fork Test",
        },
        "organization": None,
        "tags": [],
        "timeout_seconds": 120,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": False,
        "engine_token": "fork-test-dead-token",
    }


@contextlib.asynccontextmanager
async def _factory(db_session):
    yield db_session


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


@pytest.mark.asyncio
class TestForkedKnowledgeTransport:
    async def test_all_seven_ops_without_http(self, db_session, monkeypatch):
        """A real forked child runs the full knowledge facade with HTTP dead."""
        tag = uuid4().hex[:8]
        ns = f"fork-ns-{tag}"
        source = (
            "import os, sys\n"
            "from bifrost import knowledge\n"
            f"_id1 = await knowledge.store('The refund window is thirty days.', namespace={ns!r}, key='refund')\n"
            f"_id2 = await knowledge.store('Database indexes speed up queries.', namespace={ns!r}, key='db')\n"
            f"_got = await knowledge.get('refund', namespace={ns!r})\n"
            f"_found = await knowledge.search('refund policy', namespace={ns!r}, limit=5)\n"
            f"_listed = await knowledge.list_namespaces()\n"
            f"_missing = await knowledge.get('absent', namespace={ns!r})\n"
            f"_deleted = await knowledge.delete('db', namespace={ns!r})\n"
            f"_count = await knowledge.delete_namespace({ns!r})\n"
            "result = {\n"
            "    'ids_ok': bool(_id1) and bool(_id2) and _id1 != _id2,\n"
            "    'got_ok': _got is not None and 'thirty days' in _got.content,\n"
            "    'found_keys': sorted([d.key for d in _found]),\n"
            "    'listed_ok': any(n.namespace == " + repr(ns) + " for n in _listed),\n"
            "    'missing_ok': _missing is None,\n"
            "    'deleted_ok': _deleted is True,\n"
            "    'count_ok': _count == 1,\n"
            "    'had_db_url': (\n"
            "        'BIFROST_DATABASE_URL' in os.environ\n"
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
            "    ),\n"
            "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
            "}\n"
        )
        context = _context_for(_script_b64(source))
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-knowledge-fork", with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            with patch(
                "shared.sdk_knowledge.load_knowledge_embedder",
                new=AsyncMock(return_value=_FakeEmbedder()),
            ):
                pump = asyncio.create_task(
                    serve_channel(
                        recv_conn=sdk_req,
                        send_conn=sdk_resp,
                        session_factory=lambda: _factory(db_session),
                        principal=LocalDispatchPrincipal(caller_org_id=None),
                    )
                )
                work_queue.put(("exec-knowledge-fork", context))
                envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["ids_ok"] is True, result
            assert result["got_ok"] is True, result
            assert result["found_keys"] == ["db", "refund"], result
            assert result["listed_ok"] is True, result
            assert result["missing_ok"] is True, result
            assert result["deleted_ok"] is True, result
            assert result["count_ok"] is True, result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result
            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
            pump = None
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()
