"""Physical pure-agent-compute launcher lifecycle contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.worker.remote.identity import LauncherIdentity
from src.worker.remote.launcher import Launcher
from src.worker.remote.protocol import ControlEnvelope


class _Work:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def put_nowait(self, frame: dict) -> None:
        self.sent.append(frame)

    def close(self) -> None:
        pass


class _Result:
    def __init__(self) -> None:
        self.frames: queue.Queue[dict] = queue.Queue()
        self._reader, self._writer = os.pipe()

    def get_nowait(self) -> dict:
        if self.frames.empty():
            raise queue.Empty
        return self.frames.get_nowait()

    def fileno(self) -> int:
        return self._reader

    def close(self) -> None:
        os.close(self._reader)
        os.close(self._writer)


def _identity() -> LauncherIdentity:
    return LauncherIdentity(
        launcher_id=uuid4(), incarnation_id=uuid4(), job_uid=None, pod_uid=None,
        hostname="runner", form="static",
    )


def _payload() -> dict[str, object]:
    immutable = {
        "plan": {"surface": "chat"},
        "model_name": "test-model",
        "model_profile_payload_b64": "",
        "usage_initial": {},
        "user_prompt": "hello",
        "history": [],
    }
    digest = hashlib.sha256(
        json.dumps(immutable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "stage_id": str(uuid4()),
        "route_id": "agent-route-1",
        "run_id": str(uuid4()),
        "dispatch_id": str(uuid4()),
        "input_sha256": digest,
        "memory_request_bytes": 1024,
        **immutable,
    }


@pytest.mark.asyncio
async def test_agent_compute_start_fences_ambiguous_fork_failure_without_retrying() -> None:
    launcher = Launcher(AsyncMock(), identity=_identity(), secret="secret")
    launcher._agent_compute_template_ready = True
    launcher.check_fork_pressure = Mock()
    launcher.agent_compute_template = Mock()
    launcher.agent_compute_template.fork.side_effect = RuntimeError("fork transport lost")
    payload = _payload()

    assert launcher.agent_compute_status(payload)["state"] == "unknown"

    first = await launcher.agent_compute_start(payload)
    second = await launcher.agent_compute_start(payload)
    await asyncio.sleep(0)

    assert first["state"] == second["state"] == "terminal"
    assert first["terminal"]["error_type"] == "RuntimeError"
    launcher.agent_compute_template.fork.assert_called_once_with(worker_id="agent-route-1")


@pytest.mark.asyncio
async def test_launcher_stop_shuts_down_the_warmed_pure_compute_template() -> None:
    launcher = Launcher(AsyncMock(), identity=_identity(), secret="secret")
    launcher.template = Mock()
    launcher.agent_template = Mock()
    launcher.agent_compute_template = Mock()
    launcher._agent_compute_template_ready = True

    await launcher.stop()

    launcher.template.shutdown.assert_called_once_with()
    launcher.agent_template.shutdown.assert_called_once_with()
    launcher.agent_compute_template.shutdown.assert_called_once_with()


@pytest.mark.asyncio
async def test_agent_compute_rpc_reply_is_idempotent_and_terminal_is_retained_until_release() -> None:
    socket = AsyncMock()
    launcher = Launcher(socket, identity=_identity(), secret="secret")
    launcher.session_generation = 1
    launcher._agent_compute_template_ready = True
    launcher.check_fork_pressure = Mock()
    work, result = _Work(), _Result()
    launcher.agent_compute_template = Mock()
    launcher.agent_compute_template.fork.return_value = (999_999, work, result)
    payload = _payload()
    try:
        started = await launcher.agent_compute_start(payload)
        assert started["state"] == "running"
        assert work.sent == [{"kind": "init", **{key: value for key, value in payload.items() if key != "memory_request_bytes"}}]

        request_id = str(uuid4())
        result.frames.put({
            "kind": "rpc", "stage_id": payload["stage_id"], "request_id": request_id,
            "op": "effect", "payload": {"name": "read"}, "stream": False,
        })
        launcher.agent_compute_result_ready(str(payload["stage_id"]))
        await asyncio.sleep(0)
        event = ControlEnvelope.from_json(socket.send.await_args.args[0]).payload
        assert event == {
            "event": "agent_compute_rpc", **{key: payload[key] for key in (
                "stage_id", "route_id", "run_id", "dispatch_id", "input_sha256",
            )},
            "request_id": request_id, "op": "effect", "payload": {"name": "read"}, "stream": False,
        }

        reply = {**{key: payload[key] for key in ("stage_id", "route_id", "run_id", "dispatch_id", "input_sha256")},
                 "request_id": request_id, "reply_frame": {"result": {"ok": True}}}
        assert (await launcher.agent_compute_reply(reply))["accepted"] is True
        assert (await launcher.agent_compute_reply(reply))["accepted"] is True
        assert work.sent[-1] == {"kind": "rpc_reply", "request_id": request_id, "result": {"ok": True}}
        assert len(work.sent) == 2
        with pytest.raises(ValueError, match="conflicts"):
            await launcher.agent_compute_reply({
                **reply, "reply_frame": {"result": {"ok": False}},
            })
        assert len(work.sent) == 2

        result.frames.put({
            "kind": "terminal", "stage_id": payload["stage_id"], "ok": True,
            "result": {"output": "done", "usage": {}, "empty_output_guard": None},
        })
        launcher.agent_compute_result_ready(str(payload["stage_id"]))
        await asyncio.sleep(0)
        status = launcher.agent_compute_status(payload)
        assert status["state"] == "terminal"
        assert status["terminal"]["result"]["output"] == "done"
        assert str(payload["stage_id"]) not in launcher.children

        released = await launcher.agent_compute_release(payload)
        assert released["released"] is True
        assert str(payload["stage_id"]) not in launcher.agent_compute_stages
        assert (await launcher.agent_compute_release(payload))["released"] is True
        assert launcher.agent_compute_status(payload)["state"] == "released"
    finally:
        for task in list(launcher._tasks):
            task.cancel()
        await asyncio.gather(*launcher._tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_compute_failed_terminal_retains_usage_and_empty_output_evidence() -> None:
    launcher = Launcher(AsyncMock(), identity=_identity(), secret="secret")
    launcher.session_generation = 1
    launcher._agent_compute_template_ready = True
    launcher.check_fork_pressure = Mock()
    work, result = _Work(), _Result()
    launcher.agent_compute_template = Mock()
    launcher.agent_compute_template.fork.return_value = (999_998, work, result)
    payload = _payload()
    try:
        await launcher.agent_compute_start(payload)
        result.frames.put({
            "kind": "terminal", "stage_id": payload["stage_id"], "ok": False,
            "error_type": "UsageLimitExceeded", "error": "request budget exhausted",
            "result": {
                "usage": {"request_tokens": 100},
                "empty_output_guard": {"saw_empty_response": True},
            },
        })
        launcher.agent_compute_result_ready(str(payload["stage_id"]))
        await asyncio.sleep(0)

        terminal = launcher.agent_compute_status(payload)["terminal"]
        assert terminal == {
            "ok": False,
            "error_type": "UsageLimitExceeded",
            "error": "request budget exhausted",
            "result": {
                "usage": {"request_tokens": 100},
                "empty_output_guard": {"saw_empty_response": True},
            },
        }
    finally:
        for task in list(launcher._tasks):
            task.cancel()
        await asyncio.gather(*launcher._tasks, return_exceptions=True)
