"""Real forked children served by the artifact generation dispatcher.

Uses a real ``TemplateProcess`` (the same fork primitive the pool
uses): the child installs the engine-local transport at engine start
and runs an inline script through the normal execution path, while the
parent serves the four fixed generation operations from a real
database session. The child's HTTP route is hard-disabled (dead
``BIFROST_API_URL``) and its environment carries no database
credentials, so envelope success proves the local transport. Committed
output is verified afterwards via the external HTTP handlers and the
database — the local commit boundary is what makes later reads see
the rows.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

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


class _MemoryStorage:
    """Deterministic in-memory stand-in for the file storage service."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def write_raw_to_s3(self, key: str, content: bytes) -> None:
        self.objects[key] = bytes(content)

    async def write_raw_chunks_to_s3(
        self, key: str, chunks, content_type: str = "application/octet-stream"
    ) -> None:
        buf = bytearray()
        async for chunk in chunks:
            buf.extend(chunk)
        self.objects[key] = bytes(buf)

    async def read_uploaded_file(self, key: str) -> bytes:
        return self.objects[key]

    async def generate_presigned_download_url(
        self,
        key: str,
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        return f"https://artifacts.local.test/{key}?sig=test"

    async def delete_raw_from_s3(self, key: str) -> None:
        self.objects.pop(key, None)


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str, org_id=None) -> dict:
    return {
        "execution_id": str(uuid4()),
        "name": "sdk-gen-local-fork-test",
        "code": code_b64,
        "parameters": {},
        "caller": {
            "user_id": "fork-test-user",
            "email": "fork@test.local",
            "name": "Fork Test",
        },
        "organization": None if org_id is None else {"id": str(org_id)},
        "tags": [],
        "timeout_seconds": 180,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": False,
        # One-shot token; the child's HTTP route is dead by env design, so
        # any HTTP attempt fails loudly instead of succeeding silently.
        "engine_token": "fork-test-dead-token",
    }


@contextlib.asynccontextmanager
async def _factory(db_session):
    yield db_session


async def _seed_org(db_session):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-gen-fork-{uuid4().hex[:8]}",
        is_active=True,
        created_by="sdk-gen-fork-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_system_user(db_session):
    from src.core.constants import SYSTEM_USER_UUID
    from src.models.orm.users import User as UserModel

    existing = await db_session.get(UserModel, SYSTEM_USER_UUID)
    if existing is not None:
        return existing
    row = UserModel(
        id=SYSTEM_USER_UUID,
        email="engine@bifrost.internal",
        name="Bifrost Engine",
        is_active=True,
        is_superuser=True,
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


_GENERATE_SOURCE = (
    "import os, sys\n"
    "from bifrost import artifacts\n"
    "_doc = await artifacts.create_document(\n"
    "    'brief', format='pdf', title='Brief',\n"
    "    sections=[{'heading': 'Summary', 'paragraphs': ['Ready']}],\n"
    ")\n"
    "_sheet = await artifacts.create_spreadsheet(\n"
    "    'report', sheets=[{'name': 'Data', 'columns': ['A'], 'rows': [['1']]}],\n"
    ")\n"
    "_text = await artifacts.create_text(\n"
    "    'notes', format='markdown', content='# Ready',\n"
    ")\n"
    "_image = await artifacts.create_image(\n"
    "    'launch-concept', prompt='A launch concept',\n"
    ")\n"
    "_back = await artifacts.read(_text)\n"
    "result = {\n"
    "    'doc': _doc.model_dump(),\n"
    "    'sheet': _sheet.model_dump(),\n"
    "    'text': _text.model_dump(),\n"
    "    'image': _image.model_dump(),\n"
    "    'read_back_ok': _back.startswith(b'# Ready'),\n"
    "    'had_db_url': (\n"
    "        'BIFROST_DATABASE_URL' in os.environ\n"
    "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
    "    ),\n"
    "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
    "}\n"
)

_ERROR_SOURCE = (
    "from bifrost import artifacts\n"
    "from bifrost.client import BifrostAPIError\n"
    "_caught = None\n"
    "try:\n"
    "    await artifacts.create_text('notes', format='markdown', content='')\n"
    "except BifrostAPIError as e:\n"
    "    _caught = e.response.status_code\n"
    "result = {'caught_status': _caught}\n"
)


@pytest.mark.asyncio
class TestForkedArtifactGenerationTransport:
    async def test_all_four_ops_without_http_and_committed(
        self, db_session, monkeypatch
    ):
        """A real forked child generates four artifacts with HTTP dead."""
        from shared.artifact_generation import GeneratedArtifact
        from src.services.media_generation import MediaProviderConfig

        store = _MemoryStorage()
        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        # Hard-disable HTTP for every forked child of this test: any SDK
        # call that reaches HTTP fails with connection-refused, so
        # success proves the local transport served the operations.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        config = MediaProviderConfig(
            provider="openai",
            endpoint="https://api.openai.com/v1",
            api_key="secret",
            model="gpt-image-1",
            is_openrouter=False,
        )
        generated = GeneratedArtifact(
            filename="Launch Concept.png",
            content_type="image/png",
            content=b"png-data",
            provider="openai",
            model="gpt-image-1",
        )

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-gen-fork", with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            with (
                patch(
                    "src.services.artifacts.get_file_storage_service",
                    return_value=store,
                ),
                patch(
                    "src.services.media_generation.get_media_provider_config",
                    new=AsyncMock(return_value=config),
                ),
                patch(
                    "src.services.media_generation.generate_image_with_config",
                    new=AsyncMock(return_value=generated),
                ),
                patch(
                    "src.services.media_generation.record_media_usage",
                    new=AsyncMock(),
                ),
            ):
                pump = asyncio.create_task(
                    serve_channel(
                        recv_conn=sdk_req,
                        send_conn=sdk_resp,
                        session_factory=lambda: _factory(db_session),
                        principal=LocalDispatchPrincipal(caller_org_id=org.id),
                    )
                )
                work_queue.put(
                    (str(uuid4()), _context_for(_script_b64(_GENERATE_SOURCE)))
                )
                envelope = await asyncio.to_thread(result_queue.get, True, 150.0)
                assert envelope["success"] is True, envelope
                result = envelope["result"]
                assert result["read_back_ok"] is True, result
                assert result["had_db_url"] is False
                assert result["had_sqlalchemy"] is False
                for key in ("doc", "sheet", "text", "image"):
                    assert result[key]["type"] == "bifrost_artifact", result

                # Committed output is visible via the external HTTP
                # handlers and the database — the local commit boundary
                # held. The storage patch stays active: bytes were
                # written to this store by the parent dispatcher.
                from src.core.principal import UserPrincipal
                from src.routers.cli import sdk_read_artifact
                from uuid import UUID as _UUID

                engine_user = UserPrincipal(
                    user_id="00000000-0000-0000-0000-000000000001",
                    email="engine@bifrost.internal",
                    organization_id=None,
                    is_superuser=True,
                )
                for key in ("doc", "sheet", "text", "image"):
                    response = await sdk_read_artifact(
                        _UUID(result[key]["id"]), engine_user, db_session
                    )
                    assert len(response.body) > 0

            from sqlalchemy import select

            from src.models.orm import Artifact as ArtifactModel

            rows = (
                await db_session.execute(
                    select(ArtifactModel).where(
                        ArtifactModel.id.in_(
                            [result[k]["id"] for k in ("doc", "sheet", "text", "image")]
                        )
                    )
                )
            ).scalars().all()
            assert len(rows) == 4

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

    async def test_generation_error_without_http(self, db_session, monkeypatch):
        """A validation failure surfaces locally with HTTP dead."""
        store = _MemoryStorage()
        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-gen-fork-err", with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            with patch(
                "src.services.artifacts.get_file_storage_service",
                return_value=store,
            ):
                pump = asyncio.create_task(
                    serve_channel(
                        recv_conn=sdk_req,
                        send_conn=sdk_resp,
                        session_factory=lambda: _factory(db_session),
                        principal=LocalDispatchPrincipal(caller_org_id=org.id),
                    )
                )
                work_queue.put(
                    (str(uuid4()), _context_for(_script_b64(_ERROR_SOURCE)))
                )
                envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            assert envelope["result"]["caught_status"] == 422
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
