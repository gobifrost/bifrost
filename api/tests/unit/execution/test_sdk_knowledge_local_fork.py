"""Gate C4c: a real forked child reaches the knowledge routes over the socket.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses): the
child installs the worker's private Unix socket at engine start and runs an
inline script through the normal execution path, while the test process
serves the **real** knowledge routes on that socket via uvicorn against the
worker's global database engine. The parent owns the DB and the embedding
provider; the child's network API is dead by environment and it receives no
database or provider credentials. Envelope success therefore proves the
migrated facade rode the shared client transport — zero API requests for the
fixed calls, and no PostgreSQL in the child.

The embedding provider is stubbed in-process (the socket server shares this
process) so the test makes no paid provider calls.

Marked ``slow`` like the other real-fork tests: template boot costs seconds.
"""

import asyncio
import base64
import contextlib
import os
import time
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

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


def _patch_embedder():
    """Stub the provider seam for every in-process socket request."""
    return patch(
        "shared.sdk_knowledge.embeddings_factory_module.get_embedding_client",
        new=AsyncMock(return_value=_FakeEmbedder()),
    )


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str, engine_token: str) -> dict:
    return {
        "execution_id": f"knowledge-fork-{uuid4().hex[:8]}",
        "name": "sdk-knowledge-local-fork-test",
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


@pytest.mark.asyncio
class TestForkedKnowledgeTransport:
    async def test_all_seven_ops_over_worker_socket_without_channel(
        self, monkeypatch
    ):
        """A real forked child runs the full knowledge facade, HTTP dead."""
        from src.core.security import mint_engine_token

        tag = uuid4().hex[:8]
        ns = f"fork-ns-{tag}"
        source = (
            "import os, sys\n"
            "from bifrost import knowledge\n"
            "from bifrost.client import get_engine_socket_path\n"
            f"_id1 = await knowledge.store('The refund window is thirty days.', namespace={ns!r}, key='refund')\n"
            f"_id2 = await knowledge.store('Database indexes speed up queries.', namespace={ns!r}, key='db')\n"
            f"_got = await knowledge.get('refund', namespace={ns!r})\n"
            f"_found = await knowledge.search('refund policy', namespace={ns!r}, limit=5)\n"
            f"_listed = await knowledge.list_namespaces()\n"
            f"_missing = await knowledge.get('absent', namespace={ns!r})\n"
            f"_deleted = await knowledge.delete('db', namespace={ns!r})\n"
            f"_count = await knowledge.delete_namespace({ns!r})\n"
            "# A realistic batch: one request, one commit, every id returned.\n"
            f"_batch = await knowledge.store_many(\n"
            f"    [{{'content': f'Batch document {{i}}', 'key': f'b{{i}}', 'metadata': {{'i': i}}}} for i in range(40)],\n"
            f"    namespace={ns!r},\n"
            f")\n"
            f"_batch_count = await knowledge.delete_namespace({ns!r})\n"
            "result = {\n"
            "    'used_socket': get_engine_socket_path() is not None,\n"
            "    'socket_path': get_engine_socket_path(),\n"
            "    'ids_ok': bool(_id1) and bool(_id2) and _id1 != _id2,\n"
            "    'got_ok': _got is not None and 'thirty days' in _got.content,\n"
            "    'found_keys': sorted([d.key for d in _found]),\n"
            "    'listed_ok': any(n.namespace == " + repr(ns) + " for n in _listed),\n"
            "    'missing_ok': _missing is None,\n"
            "    'deleted_ok': _deleted is True,\n"
            "    'count_ok': _count == 1,\n"
            "    'batch_len': len(_batch),\n"
            "    'batch_unique': len(set(_batch)) == len(_batch),\n"
            "    'batch_count': _batch_count,\n"
            "    'had_db_url': (\n"
            "        'BIFROST_DATABASE_URL' in os.environ\n"
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
            "    ),\n"
            "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
            "}\n"
        )
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        engine_token, _ = mint_engine_token(
            execution_id="gate-c4c-fork",
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=120,
        )

        server = WorkerSdkHttpServer()
        await server.start()
        assert server.socket_path is not None

        template = TemplateProcess()
        template.start()
        try:
            with _patch_embedder():
                child_pid, work_queue, result_queue = template.fork(
                    worker_id="sdk-knowledge-fork",
                    sdk_socket_path=server.socket_path,
                )
                try:
                    work_queue.put(
                        (
                            "exec-knowledge-fork",
                            _context_for(_script_b64(source), engine_token),
                        )
                    )
                    envelope = await asyncio.to_thread(
                        result_queue.get, True, 120.0
                    )
                finally:
                    work_queue.close()
                    result_queue.close()

            assert envelope["success"] is True, envelope
            result = envelope["result"]
            # Transport proof: socket injected, network API dead, no DB or
            # SQLAlchemy in the child.
            assert result["used_socket"] is True
            assert result["socket_path"] == server.socket_path
            assert result["had_db_url"] is False
            assert result["had_sqlalchemy"] is False
            # Facade parity for all seven methods.
            assert result["ids_ok"] is True, result
            assert result["got_ok"] is True, result
            assert result["found_keys"] == ["db", "refund"], result
            assert result["listed_ok"] is True, result
            assert result["missing_ok"] is True, result
            assert result["deleted_ok"] is True, result
            assert result["count_ok"] is True, result
            # The realistic batch landed and was cleared in one namespace.
            assert result["batch_len"] == 40
            assert result["batch_unique"] is True
            assert result["batch_count"] == 40

            _wait_for_pid_to_die(child_pid)
        finally:
            with contextlib.suppress(Exception):
                template.shutdown()
            await server.stop()
