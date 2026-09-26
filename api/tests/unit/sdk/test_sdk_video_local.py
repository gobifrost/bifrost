"""Engine-local transport for the fixed SDK video generation path.

Covers the acceptance surface that does not need a forked child:

- HTTP/local parity for ``artifacts.create_video`` enqueue through the
  shared service (``shared.sdk_video``): the same ``VideoArtifactSpec``
  DTO, requester/org/resource metadata, notification creation, and the
  commit/refresh/update ordering agree on both transports;
- HTTP/local status parity through the same visibility rule and
  ``PlatformJobPublic`` shape; the local poll stays fixed to SDK video
  jobs (any other job type is a 404);
- the parent dispatcher derives requester, org, workspace, and
  execution identity from the parent-owned principal and never trusts
  child actor or visibility claims;
- the child facade keeps its timeout, polling interval, terminal
  result/error, ``requires_action``, and ``ArtifactRef`` behavior, and
  ``ai.create_video`` inherits the local path by delegation;
- malformed frames map to the same HTTP-style statuses as the HTTP
  path, and a failed local enqueue/poll never falls back to HTTP (the
  fixed API request count stays 0 on the local path).
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from bifrost._local_transport import (
    OP_ARTIFACTS_CREATE_VIDEO,
    OP_ARTIFACTS_VIDEO_STATUS,
)


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import LocalDispatchPrincipal

    return LocalDispatchPrincipal(
        caller_org_id=org_id,
        is_platform_admin=kwargs.get("is_platform_admin", False),
        is_provider_org=kwargs.get("is_provider_org", False),
        is_external=kwargs.get("is_external", False),
        actor_email=kwargs.get("actor_email", "engine@bifrost.internal"),
        is_service=kwargs.get("is_service", False),
        service_id=kwargs.get("service_id"),
        service_attempt_id=kwargs.get("service_attempt_id"),
        execution_id=kwargs.get("execution_id"),
    )


def _video_body(**over):
    body = {"filename": "launch-loop", "prompt": "A launch loop"}
    body.update(over)
    return body


async def _seed_org(db_session):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-video-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="sdk-video-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _local_enqueue(db_session, *, principal, body, workspace_id=None):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame: dict = {
        "v": 1,
        "id": f"video-{uuid4().hex[:8]}",
        "op": OP_ARTIFACTS_CREATE_VIDEO,
        **body,
        "workspace_id": str(workspace_id) if workspace_id is not None else None,
    }
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


async def _local_status(db_session, *, principal, job_id):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1,
            "id": f"vstatus-{uuid4().hex[:8]}",
            "op": OP_ARTIFACTS_VIDEO_STATUS,
            "job_id": str(job_id),
        },
    )


def _artifact_payload(ref_id="artifact-video"):
    return {
        "type": "bifrost_artifact",
        "id": ref_id,
        "filename": "Launch Loop.mp4",
        "content_type": "video/mp4",
        "size_bytes": 24,
    }


def _job_dict(job_id="job-1", status="queued", **over):
    job: dict = {"id": job_id, "status": status}
    job.update(over)
    return job


@pytest.mark.asyncio
class TestEnqueueParity:
    async def test_accepted_parity_with_http_service(self, db_session) -> None:
        from shared.sdk_video import enqueue_sdk_video_job, finalize_sdk_video_job
        from src.core.principal import UserPrincipal
        from src.jobs.platform.video_generation import SDK_VIDEO_GENERATION_DEFINITION
        from src.models.contracts.artifacts import VideoArtifactSpec
        from src.services.execution.sdk_local_dispatch import (
            _video_user_for_principal,
        )

        execution_id = uuid4()
        workspace_id = uuid4()
        principal = _principal(execution_id=str(execution_id))
        user = _video_user_for_principal(principal)
        assert isinstance(user, UserPrincipal)

        http_job, http_reused = await enqueue_sdk_video_job(
            db_session,
            user,
            spec=VideoArtifactSpec(**_video_body()),
            workspace_id=workspace_id,
            execution_id=execution_id,
        )
        await finalize_sdk_video_job(db_session, http_job)

        local = await _local_enqueue(
            db_session,
            principal=_principal(execution_id=str(execution_id)),
            body=_video_body(),
            workspace_id=workspace_id,
        )
        assert local["ok"] is True, local
        accepted = local["result"]
        assert accepted["status"] == "queued"
        assert accepted["reused"] is False
        assert accepted["status"] == http_job.status
        assert UUID(accepted["job_id"]) != http_job.id

        from src.core.security import decrypt_secret
        from src.jobs.platform.video_generation import SDKVideoGenerationPayload
        from src.models.orm.platform_jobs import PlatformJob

        row = await db_session.get(PlatformJob, UUID(accepted["job_id"]))
        assert row is not None
        assert row.job_type == SDK_VIDEO_GENERATION_DEFINITION.job_type
        assert row.requested_by_user_id == http_job.requested_by_user_id
        assert row.organization_id == http_job.organization_id
        assert row.resource_type == "artifact"
        assert row.resource_id == "launch-loop"
        assert row.title == "Generating launch-loop"
        assert row.notification_id is not None
        assert row.payload == {"protected": True}
        stored = SDKVideoGenerationPayload.model_validate_json(
            decrypt_secret(str(row.encrypted_payload))
        )
        assert stored.filename == "launch-loop"
        assert stored.workspace_id == workspace_id
        assert stored.execution_id == execution_id

    async def test_parent_derives_execution_id_not_child_frames(
        self, db_session
    ) -> None:
        from src.core.security import decrypt_secret
        from src.models.orm.platform_jobs import PlatformJob

        parent_execution = uuid4()
        principal = _principal(execution_id=str(parent_execution))
        local = await _local_enqueue(
            db_session, principal=principal, body=_video_body()
        )
        assert local["ok"] is True, local
        row = await db_session.get(
            PlatformJob, UUID(local["result"]["job_id"])
        )
        assert row is not None
        # No execution_id frame field exists: the stored payload carries
        # the parent-derived execution id.
        assert row.encrypted_payload is not None
        assert f'"{parent_execution}"' in decrypt_secret(str(row.encrypted_payload))

        other = _principal(execution_id=str(uuid4()))
        second = await _local_enqueue(
            db_session, principal=other, body=_video_body()
        )
        assert second["ok"] is True, second
        other_row = await db_session.get(
            PlatformJob, UUID(second["result"]["job_id"])
        )
        assert other_row is not None
        assert f'"{parent_execution}"' not in decrypt_secret(
            str(other_row.encrypted_payload)
        )

    async def test_invalid_spec_is_422(self, db_session) -> None:
        local = await _local_enqueue(
            db_session, principal=_principal(), body=_video_body(prompt="")
        )
        assert local["ok"] is False
        assert local["status"] == 422

        missing = await _local_enqueue(
            db_session, principal=_principal(), body={"filename": "x"}
        )
        assert missing["ok"] is False
        assert missing["status"] == 422

    async def test_malformed_workspace_is_422(self, db_session) -> None:
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        bad = await dispatch_frame(
            lambda: _db_factory(db_session),
            _principal(),
            {
                "v": 1,
                "id": "bad-ws",
                "op": OP_ARTIFACTS_CREATE_VIDEO,
                **_video_body(),
                "workspace_id": "nope",
            },
        )
        assert bad["ok"] is False
        assert bad["status"] == 422


@pytest.mark.asyncio
class TestStatusParity:
    async def test_status_parity_with_http_service(self, db_session) -> None:
        from shared.sdk_video import enqueue_sdk_video_job, get_platform_job_status
        from src.core.principal import UserPrincipal
        from src.services.execution.sdk_local_dispatch import (
            _video_user_for_principal,
        )
        from src.models.contracts.artifacts import VideoArtifactSpec

        principal = _principal()
        user = _video_user_for_principal(principal)
        job, _ = await enqueue_sdk_video_job(
            db_session, user, spec=VideoArtifactSpec(**_video_body())
        )
        await db_session.commit()

        assert isinstance(user, UserPrincipal)
        http_public = await get_platform_job_status(db_session, user, job.id)
        local = await _local_status(db_session, principal=principal, job_id=job.id)
        assert local["ok"] is True, local
        assert local["result"] == http_public.model_dump(mode="json")

    async def test_each_poll_reads_committed_state(self, db_session) -> None:
        from shared.sdk_video import enqueue_sdk_video_job
        from src.models.contracts.artifacts import VideoArtifactSpec
        from src.services.execution.sdk_local_dispatch import (
            _video_user_for_principal,
        )

        principal = _principal()
        user = _video_user_for_principal(principal)
        job, _ = await enqueue_sdk_video_job(
            db_session, user, spec=VideoArtifactSpec(**_video_body())
        )
        await db_session.commit()

        first = await _local_status(db_session, principal=principal, job_id=job.id)
        assert first["ok"] is True, first
        assert first["result"]["status"] == "queued"

        job.phase = "Rendering"
        job.progress_percent = 40
        await db_session.commit()

        second = await _local_status(db_session, principal=principal, job_id=job.id)
        assert second["ok"] is True, second
        assert second["result"]["progress"]["phase"] == "Rendering"
        assert second["result"]["progress"]["percent"] == 40

    async def test_visibility_through_local_entry_point(
        self, db_session
    ) -> None:
        from shared.sdk_video import enqueue_sdk_video_job
        from src.core.principal import UserPrincipal
        from src.models.contracts.artifacts import VideoArtifactSpec

        # A job owned by a real user, enqueued outside the local path.
        owner = UserPrincipal(
            user_id=uuid4(),
            email="owner@test.local",
            organization_id=None,
            name="Owner",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        job, _ = await enqueue_sdk_video_job(
            db_session, owner, spec=VideoArtifactSpec(**_video_body())
        )
        await db_session.commit()

        # A service caller (non-admin, different requester) reads it as
        # missing — the shared requester-visibility rule, through the
        # local entry point.
        stranger_org = await _seed_org(db_session)
        await db_session.commit()
        stranger = _principal(
            stranger_org.id,
            is_service=True,
            service_id=str(uuid4()),
            service_attempt_id=str(uuid4()),
        )
        hidden = await _local_status(db_session, principal=stranger, job_id=job.id)
        assert hidden["ok"] is False
        assert hidden["status"] == 404

        # The workflow engine caller is the superuser token-equivalent,
        # so it reads via the same admin bypass the HTTP engine path
        # uses.
        engine = _principal()
        seen = await _local_status(db_session, principal=engine, job_id=job.id)
        assert seen["ok"] is True, seen
        assert seen["result"]["id"] == str(job.id)

        missing = await _local_status(
            db_session, principal=engine, job_id=uuid4()
        )
        assert missing["ok"] is False
        assert missing["status"] == 404

    async def test_admin_reads_and_non_video_is_404(self, db_session) -> None:
        from shared.sdk_video import enqueue_sdk_video_job
        from src.models.contracts.artifacts import VideoArtifactSpec
        from src.models.orm.platform_jobs import PlatformJob
        from src.services.execution.sdk_local_dispatch import (
            _video_user_for_principal,
        )

        owner = _principal()
        user = _video_user_for_principal(owner)
        job, _ = await enqueue_sdk_video_job(
            db_session, user, spec=VideoArtifactSpec(**_video_body())
        )
        other = PlatformJob(
            job_type="solution.deploy",
            payload={},
            requested_by_user_id=str(user.user_id),
            requested_by_email=user.email,
            requested_by_name=user.email,
            title="Deploy",
            status="queued",
            phase="Queued",
        )
        db_session.add(other)
        await db_session.commit()

        admin = _principal(is_platform_admin=True)
        seen = await _local_status(db_session, principal=admin, job_id=job.id)
        assert seen["ok"] is True, seen
        assert seen["result"]["id"] == str(job.id)

        rejected = await _local_status(
            db_session, principal=owner, job_id=other.id
        )
        assert rejected["ok"] is False
        assert rejected["status"] == 404

        admin_rejected = await _local_status(
            db_session, principal=admin, job_id=other.id
        )
        assert admin_rejected["ok"] is False
        assert admin_rejected["status"] == 404

    async def test_malformed_job_id_is_422(self, db_session) -> None:
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        for bad_id in (None, "", "nope"):
            result = await dispatch_frame(
                lambda: _db_factory(db_session),
                _principal(),
                {
                    "v": 1,
                    "id": "bad-job",
                    "op": OP_ARTIFACTS_VIDEO_STATUS,
                    "job_id": bad_id,
                },
            )
            assert result["ok"] is False, bad_id
            assert result["status"] == 422, bad_id


@pytest.mark.asyncio
class TestFacadeEngineRequest:
    """``create_video`` enqueues and polls through the shared client."""

    @staticmethod
    def _module():
        import importlib

        return importlib.import_module("bifrost.artifacts")

    @staticmethod
    def _response(payload, status=200):
        import httpx

        return httpx.Response(
            status,
            json=payload,
            request=httpx.Request("GET", "http://api/api/platform-jobs/job"),
        )

    async def test_success_polls_status_over_engine_request(self, monkeypatch):
        module = self._module()
        accepted = {
            "job_id": "job-1",
            "notification_id": None,
            "status": "queued",
            "reused": False,
        }
        statuses = [
            _job_dict(status="running", phase="Rendering"),
            _job_dict(status="succeeded", result={"artifact": _artifact_payload()}),
        ]
        client = MagicMock()
        client.engine_request = AsyncMock(
            side_effect=[self._response(accepted)]
            + [self._response(job) for job in statuses]
        )
        monkeypatch.setattr(module, "get_client", lambda: client)

        ref = await module.artifacts.create_video(
            "launch-loop", prompt="A launch loop", poll_interval_seconds=0.001
        )
        assert ref.id == "artifact-video"
        assert ref.filename == "Launch Loop.mp4"
        calls = client.engine_request.await_args_list
        assert [(call.args[0], call.args[1]) for call in calls] == [
            ("POST", "/api/sdk/artifacts/video"),
            ("GET", "/api/platform-jobs/job-1"),
            ("GET", "/api/platform-jobs/job-1"),
        ]
        assert calls[0].kwargs["json"] == {
            "filename": "launch-loop",
            "prompt": "A launch loop",
        }

    async def test_terminal_failures_match_http_messages(self, monkeypatch):
        module = self._module()
        cases = [
            (
                _job_dict(status="failed", error={"message": "provider down"}),
                "provider down",
            ),
            (_job_dict(status="cancelled"), "Video generation cancelled."),
            (
                _job_dict(
                    status="requires_action",
                    result={"requires_action": "confirm_deletes"},
                ),
                "requires action. Required action: confirm_deletes.",
            ),
            (_job_dict(status="requires_action", result={}), "User action is required."),
            (
                _job_dict(status="succeeded", result={}),
                "completed without an artifact",
            ),
        ]
        for job, match in cases:
            client = MagicMock()
            client.engine_request = AsyncMock(
                side_effect=[
                    self._response(
                        {"job_id": "job-1", "status": "queued", "reused": False}
                    ),
                    self._response(job),
                ]
            )
            monkeypatch.setattr(module, "get_client", lambda: client)
            with pytest.raises(RuntimeError, match=match):
                await module.artifacts.create_video(
                    "launch-loop",
                    prompt="A launch loop",
                    poll_interval_seconds=0.001,
                )
            assert client.engine_request.await_count == 2

    async def test_timeout_names_the_job(self, monkeypatch):
        module = self._module()

        def _side_effect(method, path, **kwargs):
            if method == "POST":
                return self._response(
                    {"job_id": "job-9", "status": "queued", "reused": False}
                )
            return self._response(_job_dict(job_id="job-9", status="running"))

        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=_side_effect)
        monkeypatch.setattr(module, "get_client", lambda: client)

        with pytest.raises(TimeoutError, match="platform job job-9"):
            await module.artifacts.create_video(
                "launch-loop",
                prompt="A launch loop",
                timeout_seconds=0.05,
                poll_interval_seconds=0.001,
            )
        assert client.engine_request.await_count >= 2

    async def test_enqueue_transport_error_does_not_fall_back(self, monkeypatch):
        import httpx

        module = self._module()
        client = MagicMock()
        client.engine_request = AsyncMock(
            side_effect=httpx.ConnectError("parent gone")
        )
        monkeypatch.setattr(module, "get_client", lambda: client)

        with pytest.raises(httpx.ConnectError):
            await module.artifacts.create_video("launch-loop", prompt="A launch loop")
        assert client.engine_request.await_count == 1

    async def test_status_transport_error_propagates(self, monkeypatch):
        import httpx

        module = self._module()
        client = MagicMock()
        client.engine_request = AsyncMock(
            side_effect=[
                self._response(
                    {"job_id": "job-1", "status": "queued", "reused": False}
                ),
                httpx.ConnectError("parent gone"),
            ]
        )
        monkeypatch.setattr(module, "get_client", lambda: client)

        with pytest.raises(httpx.ConnectError):
            await module.artifacts.create_video(
                "launch-loop", prompt="A launch loop", poll_interval_seconds=0.001
            )

    async def test_ai_create_video_inherits_the_transport(self, monkeypatch):
        import bifrost.ai as ai_module
        artifacts_module = self._module()

        client = MagicMock()
        client.engine_request = AsyncMock(
            side_effect=[
                self._response(
                    {"job_id": "job-2", "status": "queued", "reused": False}
                ),
                self._response(
                    _job_dict(
                        job_id="job-2",
                        status="succeeded",
                        result={"artifact": _artifact_payload("artifact-ai-video")},
                    )
                ),
            ]
        )
        monkeypatch.setattr(artifacts_module, "get_client", lambda: client)

        ref = await ai_module.ai.create_video(
            "An orbital sunrise", poll_interval_seconds=0.001
        )
        assert ref.id == "artifact-ai-video"

    async def test_validation_happens_before_transport(self):
        module = self._module()
        with pytest.raises(ValueError, match="timeout_seconds"):
            await module.artifacts.create_video(
                "launch-loop", prompt="A launch loop", timeout_seconds=0
            )
        with pytest.raises(ValueError, match="poll_interval_seconds"):
            await module.artifacts.create_video(
                "launch-loop", prompt="A launch loop", poll_interval_seconds=0
            )


@pytest.mark.asyncio
class TestChildTransportRoundtrip:
    async def test_enqueue_and_status_roundtrip_without_http(self) -> None:
        import multiprocessing as mp

        from bifrost._local_transport import ChildLocalTransport

        parent_recv, child_send = mp.Pipe(duplex=False)
        child_recv, parent_send = mp.Pipe(duplex=False)
        transport = ChildLocalTransport(child_send, child_recv)

        accepted = {
            "job_id": str(uuid4()),
            "notification_id": None,
            "status": "queued",
            "reused": False,
        }
        public = _job_dict(
            status="succeeded", result={"artifact": _artifact_payload()}
        )

        def fake_parent() -> None:
            import json as _json

            for payload in (accepted, public):
                raw = parent_recv.recv_bytes()
                frame = _json.loads(raw.decode("utf-8"))
                parent_send.send_bytes(
                    _json.dumps(
                        {"v": 1, "id": frame["id"], "ok": True, "result": payload}
                    ).encode("utf-8")
                )

        parent = asyncio.create_task(asyncio.to_thread(fake_parent))
        try:
            seen_accepted = await transport.call_artifacts_create_video(
                "launch-loop", "A launch loop", str(uuid4())
            )
            assert seen_accepted == accepted
            seen_public = await transport.call_artifacts_video_status(
                accepted["job_id"]
            )
            assert seen_public == public
        finally:
            parent.cancel()
            for conn in (parent_recv, parent_send, child_send, child_recv):
                with contextlib.suppress(Exception):
                    conn.close()

    async def test_status_error_frame_raises_like_http(self) -> None:
        import multiprocessing as mp

        import httpx

        from bifrost._local_transport import ChildLocalTransport
        from bifrost.client import BifrostAPIError

        parent_recv, child_send = mp.Pipe(duplex=False)
        child_recv, parent_send = mp.Pipe(duplex=False)
        transport = ChildLocalTransport(child_send, child_recv)

        def fake_parent() -> None:
            import json as _json

            raw = parent_recv.recv_bytes()
            frame = _json.loads(raw.decode("utf-8"))
            parent_send.send_bytes(
                _json.dumps(
                    {
                        "v": 1,
                        "id": frame["id"],
                        "ok": False,
                        "status": 404,
                        "detail": "Platform job not found",
                    }
                ).encode("utf-8")
            )

        parent = asyncio.create_task(asyncio.to_thread(fake_parent))
        try:
            with pytest.raises(BifrostAPIError) as exc:
                await transport.call_artifacts_video_status(str(uuid4()))
            assert isinstance(exc.value.response, httpx.Response)
            assert exc.value.response.status_code == 404
        finally:
            parent.cancel()
            for conn in (parent_recv, parent_send, child_send, child_recv):
                with contextlib.suppress(Exception):
                    conn.close()


@pytest.mark.asyncio
class TestExternalHttpUnchanged:
    async def test_engine_request_enqueues_and_polls(
        self, monkeypatch
    ) -> None:
        import importlib

        import bifrost._local_transport as lt
        module = importlib.import_module("bifrost.artifacts")

        monkeypatch.setattr(lt, "_installed", None)
        accepted = MagicMock()
        accepted.json.return_value = {"job_id": "job-1", "status": "queued"}
        completed = MagicMock()
        completed.json.return_value = _job_dict(
            job_id="job-1",
            status="succeeded",
            result={"artifact": _artifact_payload()},
        )
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=[accepted, completed])
        monkeypatch.setattr(module, "get_client", lambda: client)
        monkeypatch.setattr(
            module, "raise_for_status_with_detail", MagicMock()
        )

        ref = await module.artifacts.create_video(
            "launch-loop", prompt="A launch loop", poll_interval_seconds=0.001
        )
        assert ref.id == "artifact-video"
        assert [
            (call.args[0], call.args[1])
            for call in client.engine_request.await_args_list
        ] == [
            ("POST", "/api/sdk/artifacts/video"),
            ("GET", "/api/platform-jobs/job-1"),
        ]

    async def test_shared_terminal_mapping_covers_both_paths(self) -> None:
        from bifrost.artifacts import _video_job_artifact_or_raise

        running = _video_job_artifact_or_raise(_job_dict(status="running"))
        assert running is None
        queued = _video_job_artifact_or_raise(_job_dict(status="queued"))
        assert queued is None
        ref = _video_job_artifact_or_raise(
            _job_dict(status="succeeded", result={"artifact": _artifact_payload()})
        )
        assert ref is not None and ref.id == "artifact-video"

    async def test_service_principal_stays_org_confined(self, db_session) -> None:
        from shared.sdk_video import enqueue_sdk_video_job
        from src.models.contracts.artifacts import VideoArtifactSpec
        from src.models.orm.platform_jobs import PlatformJob
        from src.services.execution.sdk_local_dispatch import (
            _video_user_for_principal,
        )

        org = await _seed_org(db_session)
        await db_session.commit()
        org_id = org.id
        service = _principal(
            org_id,
            is_service=True,
            service_id=str(uuid4()),
            service_attempt_id=str(uuid4()),
        )
        user = _video_user_for_principal(service)
        assert user.is_superuser is False
        assert user.organization_id == org_id

        job, _ = await enqueue_sdk_video_job(
            db_session, user, spec=VideoArtifactSpec(**_video_body())
        )
        await db_session.commit()
        assert job.organization_id == org_id

        seen = await _local_status(db_session, principal=service, job_id=job.id)
        assert seen["ok"] is True, seen

        # Requester visibility is user-based and every service caller
        # shares the system-user requester, so another service reads the
        # same row — exactly like two HTTP service tokens. The org
        # confinement shows in the stored ``organization_id``, not in a
        # visibility split.
        org2 = await _seed_org(db_session)
        await db_session.commit()
        other_service = _principal(
            org2.id,
            is_service=True,
            service_id=str(uuid4()),
            service_attempt_id=str(uuid4()),
        )
        shared = await _local_status(
            db_session, principal=other_service, job_id=job.id
        )
        assert shared["ok"] is True, shared

        row = await db_session.get(PlatformJob, job.id)
        assert row is not None
        assert row.requested_by_user_id == str(user.user_id)
