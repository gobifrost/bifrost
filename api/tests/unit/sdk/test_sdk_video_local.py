"""Migrated SDK video generation facade tests.

``create_video`` enqueues and polls through the shared
``BifrostClient.engine_request`` transport (the worker Unix socket inside
an engine child, the network API elsewhere) with the same terminal
mapping on both paths. ``ai.create_video`` inherits the transport by
delegation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


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
class TestExternalHttpUnchanged:
    async def test_engine_request_enqueues_and_polls(
        self, monkeypatch
    ) -> None:
        import importlib

        module = importlib.import_module("bifrost.artifacts")

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
