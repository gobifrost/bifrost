"""Engine-local AI facade through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child is forked with the worker's private Unix socket injected exactly
as the pool does, then runs ``ai.complete`` and ``ai.get_model_info``
through the full ``bifrost.ai`` facade. The test process serves the
**real** AI routes on that socket via uvicorn against the worker's global
database engine, with the child's network API dead and the legacy channel
transport absent. Success proves the migrated calls reached the
parent-served routes, and the parent re-reads the committed usage row over
its own session to prove the real commit boundary (the socket route commits
where the HTTP ``get_db`` dependency commits). The provider is faked in the
parent (no paid external API); no real key is used.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID as _UUID
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

pytestmark = pytest.mark.slow


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str, engine_token: str, execution_id: str) -> dict:
    return {
        "execution_id": execution_id,
        "name": "sdk-ai-local-fork-test",
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


async def _run_socket_fork(server: WorkerSdkHttpServer, context: dict) -> dict:
    """Fork one child against the worker socket and return its envelope."""
    template = TemplateProcess()
    template.start()
    try:
        child_pid, work_queue, result_queue = template.fork(
            worker_id="sdk-ai-fork",
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
    "from bifrost.ai import ai\n"
    "from bifrost.client import get_engine_socket_path\n"
    "from bifrost._local_transport import get as _get_transport\n"
    "_used_socket = get_engine_socket_path() is not None\n"
    "_used_channel = _get_transport() is not None\n"
    "_resp = await ai.complete(\n"
    "    'Hello', org_id=ORG_ID, profile='Reasoning', model='gpt-4o',\n"
    "    max_tokens=42, timeout=60.0,\n"
    ")\n"
    "_info = await ai.get_model_info()\n"
    "result = {\n"
    "    'used_socket': _used_socket,\n"
    "    'used_channel': _used_channel,\n"
    "    'content': _resp.content,\n"
    "    'model': _resp.model,\n"
    "    'input_tokens': _resp.input_tokens,\n"
    "    'output_tokens': _resp.output_tokens,\n"
    "    'provider': _info['provider'],\n"
    "    'info_model': _info['model'],\n"
    "    'had_db_url': (\n"
    "        'BIFROST_DATABASE_URL' in os.environ\n"
    "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
    "    ),\n"
    "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
    "}\n"
)


@pytest.mark.asyncio
class TestForkedAISocket:
    async def test_complete_and_model_info_over_socket_and_committed(
        self, async_session_factory, monkeypatch
    ):
        """A real forked child completes + reads model info over the socket."""
        from src.core.constants import SYSTEM_USER_UUID
        from src.core.security import mint_engine_token
        from src.models.orm.ai_usage import AIUsage
        from src.models.orm.executions import Execution
        from src.models.orm.organizations import Organization as OrganizationModel

        stem = f"sdk-ai-fork-{uuid4().hex[:8]}"
        async with async_session_factory() as seed:
            org = OrganizationModel(
                name=stem,
                is_active=True,
                created_by="sdk-ai-fork-test",
            )
            seed.add(org)
            await seed.flush()
            execution_id = str(uuid4())
            seed.add(
                Execution(
                    id=_UUID(execution_id),
                    workflow_name="sdk-ai-fork-test",
                    executed_by_name="sdk-ai-fork-test",
                    organization_id=org.id,
                )
            )
            await seed.commit()
            org_id = str(org.id)

        # Hard-disable the network API for the forked child: any SDK call that
        # fell back to HTTP would fail with connection-refused.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        fake_response = SimpleNamespace(
            content="Forked hello",
            input_tokens=7,
            output_tokens=9,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider_cost=None,
            model="gpt-4o",
        )
        fake_client = AsyncMock()
        fake_client.provider_name = "openai"
        fake_client.model_name = "gpt-4o"
        fake_client.complete.return_value = fake_response
        import src.services.llm as llm_pkg
        import src.services.llm.factory as llm_factory

        monkeypatch.setattr(
            llm_pkg, "get_llm_client", AsyncMock(return_value=fake_client)
        )
        monkeypatch.setattr(
            llm_factory,
            "get_llm_config",
            AsyncMock(
                return_value=SimpleNamespace(provider="openai", model="gpt-4o")
            ),
        )

        engine_token, _ = mint_engine_token(
            execution_id="gate-c5f-ai-fork",
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=120,
        )
        context = _context_for(
            _script_b64(_AI_SOURCE.replace("ORG_ID", repr(org_id))),
            engine_token,
            execution_id,
        )

        server = WorkerSdkHttpServer()
        await server.start()
        assert server.socket_path is not None
        try:
            envelope = await _run_socket_fork(server, context)
        finally:
            await server.stop()

        try:
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_socket"] is True, result
            assert result["used_channel"] is False, result
            assert result["content"] == "Forked hello"
            assert result["model"] == "gpt-4o"
            assert result["input_tokens"] == 7
            assert result["output_tokens"] == 9
            assert result["provider"] == "openai"
            assert result["info_model"] == "gpt-4o"
            assert result["had_db_url"] is False
            assert result["had_sqlalchemy"] is False

            # The fake provider saw the profile/model/max-tokens selection.
            assert fake_client.complete.await_count == 1
            sent_kwargs = fake_client.complete.await_args.kwargs
            assert sent_kwargs["max_tokens"] == 42
            assert sent_kwargs["model"] == "gpt-4o"
            assert sent_kwargs["messages"][-1].content == "Hello"

            # Committed usage is visible via the database with the
            # parent-derived attribution (sentinel user, requested org,
            # parent execution id) — the socket route's commit boundary held.
            async with async_session_factory() as session:
                rows = (
                    await session.execute(
                        select(AIUsage).where(
                            AIUsage.execution_id == _UUID(execution_id)
                        )
                    )
                ).scalars().all()
                assert len(rows) == 1
                assert rows[0].organization_id == org.id
                assert rows[0].user_id == SYSTEM_USER_UUID
                assert rows[0].model is not None
        finally:
            async with async_session_factory() as cleanup:
                await cleanup.execute(
                    delete(AIUsage).where(AIUsage.execution_id == _UUID(execution_id))
                )
                await cleanup.execute(
                    delete(Execution).where(Execution.id == _UUID(execution_id))
                )
                await cleanup.execute(
                    delete(OrganizationModel).where(OrganizationModel.id == org.id)
                )
                await cleanup.commit()
