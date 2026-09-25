"""Real forked children served by the SDK video dispatcher.

Uses a real ``TemplateProcess`` (the same fork primitive the pool
uses): the child installs the engine-local transport at engine start
and runs an inline script through the normal execution path, while the
parent serves the two fixed video operations (enqueue + status) from
short database sessions. The child's HTTP route is hard-disabled (dead
``BIFROST_API_URL``) and its environment carries no database
credentials, so envelope success proves the local transport. The
scheduler runs in the test stack, but the test defers this job before
commit so the scheduler cannot claim it. A fake completion marks the job
succeeded with a fabricated artifact, and the child's poll loop
observes it through the real shared status service. Committed
job/notification metadata is verified afterwards via the database —
the local commit boundary is what makes later reads see the rows.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.services.execution.sdk_local_dispatch import (
    LocalDispatchPrincipal,
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
        "name": "sdk-video-local-fork-test",
        "code": code_b64,
        "parameters": {},
        "caller": {
            "user_id": "fork-test-user",
            "email": "fork@test.local",
            "name": "Fork Test",
        },
        "organization": None,
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
        name=f"sdk-video-fork-{uuid4().hex[:8]}",
        is_active=True,
        created_by="sdk-video-fork-test",
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


_VIDEO_SOURCE = (
    "import os, sys\n"
    "from bifrost import artifacts\n"
    "_ref = await artifacts.create_video(\n"
    "    'clip', prompt='An orbital sunrise',\n"
    "    timeout_seconds=60, poll_interval_seconds=0.5,\n"
    ")\n"
    "result = {\n"
    "    'artifact': _ref.model_dump(),\n"
    "    'had_db_url': (\n"
    "        'BIFROST_DATABASE_URL' in os.environ\n"
    "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
    "    ),\n"
    "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
    "}\n"
)


async def _fake_video_completion(async_session_factory, filename: str) -> str:
    """Mark the test's enqueued video job succeeded with a fake artifact.

    The enqueue fixture defers this job's availability before commit, so
    the live scheduler cannot claim it. Find the uniquely named job and store a fabricated
    ``{"artifact": ArtifactRef}`` result and commits. Scoped by the
    requested filename so concurrent suites cannot complete
    each other's jobs. Returns the completed job id.
    """
    from src.models.orm.platform_jobs import PlatformJob

    artifact_id = str(uuid4())
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        async with async_session_factory() as session:
            target = (
                await session.execute(
                    select(PlatformJob)
                    .where(
                        PlatformJob.job_type == "sdk.video_generation",
                        PlatformJob.resource_id == filename,
                    )
                )
            ).scalar_one_or_none()
            if target is not None and target.status != "succeeded":
                assert target.status == "queued"
                target.status = "succeeded"
                target.phase = "Completed"
                target.progress_percent = 100
                target.result = {
                    "artifact": {
                        "type": "bifrost_artifact",
                        "id": artifact_id,
                        "filename": "Clip.mp4",
                        "content_type": "video/mp4",
                        "size_bytes": 10,
                    }
                }
                target.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return str(target.id)
            if target is not None:
                return str(target.id)
        await asyncio.sleep(0.2)
    raise TimeoutError("video job was never enqueued by the forked child")


@pytest.mark.asyncio
class TestForkedVideoTransport:
    async def test_create_video_without_http_and_committed(
        self, db_session, async_session_factory, monkeypatch
    ):
        """A real forked child enqueues + polls video with HTTP dead."""
        from src.core.constants import SYSTEM_USER_UUID

        org = await _seed_org(db_session)
        await db_session.commit()
        # Hard-disable HTTP for every forked child of this test: any SDK
        # call that reaches HTTP fails with connection-refused, so
        # success proves the local transport served the operations.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        # Notification delivery uses a process-global Redis client in the
        # test stack, which can retain a previous pytest event loop. Keep
        # this fork focused on the committed job/notification metadata;
        # the shared-service tests cover the real notification call order.
        from src.services import platform_jobs

        async def attach_test_notification(session, job):
            job.notification_id = uuid4()
            await session.flush()
            return job.notification_id

        monkeypatch.setattr(
            platform_jobs, "ensure_platform_job_notification", attach_test_notification
        )
        monkeypatch.setattr(
            platform_jobs, "publish_platform_job_update", AsyncMock()
        )

        execution_id = str(uuid4())
        filename = f"clip-{uuid4().hex[:8]}"
        from shared import sdk_video

        real_enqueue = sdk_video.enqueue_sdk_video_job

        async def enqueue_deferred(*args, **kwargs):
            job, reused = await real_enqueue(*args, **kwargs)
            job.available_at = datetime.now(timezone.utc) + timedelta(hours=1)
            return job, reused

        monkeypatch.setattr(sdk_video, "enqueue_sdk_video_job", enqueue_deferred)
        principal = principal_from_context(
            {
                "organization": {"id": str(org.id)},
                "is_platform_admin": False,
                "execution_id": execution_id,
            }
        )
        assert isinstance(principal, LocalDispatchPrincipal)

        template = TemplateProcess()
        template.start()
        pump = None
        completer = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-video-fork", with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            # Fresh short sessions per operation (like production): each
            # poll reads committed state instead of a cached instance,
            # so the fake completion below is observable.
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
            completer = asyncio.create_task(
                _fake_video_completion(async_session_factory, filename)
            )
            work_queue.put(
                (
                    str(uuid4()),
                    _context_for(
                        _script_b64(_VIDEO_SOURCE.replace("'clip'", repr(filename))),
                        execution_id,
                    ),
                )
            )
            envelope = await asyncio.to_thread(result_queue.get, True, 150.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["had_db_url"] is False
            assert result["had_sqlalchemy"] is False
            assert result["artifact"]["type"] == "bifrost_artifact", result
            assert result["artifact"]["filename"] == "Clip.mp4", result

            job_id = await asyncio.wait_for(completer, timeout=15.0)
            completer = None

            # Committed job/notification metadata is visible via the
            # database — the local commit boundary held.
            from src.models.orm.platform_jobs import PlatformJob
            from uuid import UUID as _UUID

            async with async_session_factory() as session:
                row = await session.get(PlatformJob, _UUID(job_id))
                assert row is not None
                assert row.job_type == "sdk.video_generation"
                assert row.requested_by_user_id == str(SYSTEM_USER_UUID)
                assert row.resource_type == "artifact"
                assert row.resource_id == filename
                assert row.title == f"Generating {filename}"
                assert row.notification_id is not None
                assert row.status == "succeeded"
                assert row.payload == {"protected": True}
                assert row.encrypted_payload is not None
                from src.core.security import decrypt_secret
                from src.jobs.platform.video_generation import SDKVideoGenerationPayload

                payload = SDKVideoGenerationPayload.model_validate_json(
                    decrypt_secret(row.encrypted_payload)
                )
                assert payload.filename == filename
                assert payload.prompt == "An orbital sunrise"
                assert str(payload.execution_id) == execution_id
                assert row.result["artifact"]["id"] == result["artifact"]["id"]

            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
            pump = None
        finally:
            if completer is not None:
                completer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await completer
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()
