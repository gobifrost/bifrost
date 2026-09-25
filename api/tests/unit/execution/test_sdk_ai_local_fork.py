"""Real forked children served by the SDK AI dispatcher.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the engine-local transport at engine start and runs an
inline script through the normal execution path, while the parent serves
``ai.complete`` and ``ai.model_info`` from short database sessions. The
child's fixed-operation HTTP route is hard-disabled (dead
``BIFROST_API_URL`` plus a dead ``bifrost.ai.get_client``), and its
environment carries no database credentials, so envelope success proves
the local transport. The provider is faked in the parent (no paid
external API); committed usage metadata is verified afterwards via the
database — the local commit boundary is what makes later reads see the
row.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID as _UUID
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.services.execution.sdk_local_dispatch import (
    principal_from_context,
    serve_channel,
)
from src.services.execution.template_process import TemplateProcess

pytestmark = pytest.mark.slow


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str, execution_id: str) -> dict:
    return {
        "execution_id": execution_id,
        "name": "sdk-ai-local-fork-test",
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


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


_AI_SOURCE = (
    "import os, sys\n"
    "import importlib\n"
    "from bifrost._local_transport import get as _get_transport\n"
    "_used_local = _get_transport() is not None\n"
    "_amod = importlib.import_module('bifrost.ai')\n"
    "def _dead(*args, **kwargs):\n"
    "    raise AssertionError('fixed-operation HTTP must not be used in the engine path')\n"
    "_amod.get_client = _dead\n"
    "from bifrost.ai import ai\n"
    "_resp = await ai.complete(\n"
    "    'Hello', org_id=ORG_ID, profile='Reasoning', model='gpt-4o',\n"
    "    max_tokens=42, timeout=60.0,\n"
    ")\n"
    "_info = await ai.get_model_info()\n"
    "result = {\n"
    "    'used_local': _used_local,\n"
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
class TestForkedAITransport:
    async def test_complete_and_model_info_without_http_and_committed(
        self, db_session, async_session_factory, monkeypatch
    ):
        """A real forked child completes + reads model info with HTTP dead."""
        from src.core.constants import SYSTEM_USER_UUID
        from src.models.orm.ai_usage import AIUsage
        from src.models.orm.executions import Execution
        from src.models.orm.organizations import Organization as OrganizationModel

        org = OrganizationModel(
            name=f"sdk-ai-fork-{uuid4().hex[:8]}",
            is_active=True,
            created_by="sdk-ai-fork-test",
        )
        db_session.add(org)
        await db_session.flush()
        execution_id = str(uuid4())
        db_session.add(
            Execution(
                id=_UUID(execution_id),
                workflow_name="sdk-ai-fork-test",
                executed_by_name="sdk-ai-fork-test",
                organization_id=org.id,
            )
        )
        await db_session.flush()
        await db_session.commit()
        org_id = str(org.id)

        # Hard-disable HTTP for every forked child of this test: any SDK
        # call that reaches HTTP fails loudly instead of succeeding.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        # Fake the provider in the parent: no paid external API. The
        # completion echoes fixed content/tokens; usage recording runs
        # for real (proving the commit boundary below).
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

        principal = principal_from_context(
            {
                "organization": {"id": org_id},
                "is_platform_admin": False,
                "execution_id": execution_id,
            }
        )

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-ai-fork", with_sdk=True
            )
            conns = [sdk_req, sdk_resp]

            @contextlib.asynccontextmanager
            async def _fresh_factory():
                async with async_session_factory() as session:
                    yield session

            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=lambda: _fresh_factory(),
                    principal=principal,
                )
            )
            work_queue.put(
                (
                    str(uuid4()),
                    _context_for(
                        _script_b64(_AI_SOURCE.replace("ORG_ID", repr(org_id))),
                        execution_id,
                    ),
                )
            )
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_local"] is True
            assert result["had_db_url"] is False
            assert result["had_sqlalchemy"] is False
            assert result["content"] == "Forked hello"
            assert result["model"] == "gpt-4o"
            assert result["input_tokens"] == 7
            assert result["output_tokens"] == 9
            assert result["provider"] == "openai"
            assert result["info_model"] == "gpt-4o"

            # The fake provider saw the profile/model/max-tokens selection.
            assert fake_client.complete.await_count == 1
            sent_kwargs = fake_client.complete.await_args.kwargs
            assert sent_kwargs["max_tokens"] == 42
            assert sent_kwargs["model"] == "gpt-4o"
            sent_messages = sent_kwargs["messages"]
            assert sent_messages[-1].content == "Hello"

            # Committed usage is visible via the database with the
            # parent-derived attribution (sentinel user, requested org,
            # parent execution id) — the local commit boundary held.
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
