"""Physical launcher transport fencing contracts."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.worker.remote.identity import LauncherIdentity
from src.worker.remote.launcher import Launcher
from src.worker.remote.protocol import ControlEnvelope


@pytest.mark.asyncio
async def test_reconnect_blocks_events_until_welcome_fences_the_new_socket() -> None:
    identity = LauncherIdentity(
        launcher_id=uuid4(), incarnation_id=uuid4(), job_uid=None, pod_uid=None,
        hostname="runner", form="static",
    )
    old_socket = AsyncMock()
    new_socket = AsyncMock()
    welcome_pending = asyncio.Event()
    release_welcome = asyncio.Event()

    async def receive_welcome() -> str:
        welcome_pending.set()
        await release_welcome.wait()
        return json.dumps({"event": "welcome", "session_generation": 2})

    new_socket.recv.side_effect = receive_welcome
    launcher = Launcher(old_socket, identity=identity, secret="secret")
    launcher.session_generation = 1

    reconnect = asyncio.create_task(launcher.reconnect(new_socket, {}))
    await welcome_pending.wait()
    sample = asyncio.create_task(launcher.send({"event": "sample"}))
    await asyncio.sleep(0)

    assert new_socket.send.await_count == 1
    release_welcome.set()
    await asyncio.gather(reconnect, sample)

    assert old_socket.send.await_count == 0
    sample_frame = ControlEnvelope.from_json(new_socket.send.await_args_list[1].args[0])
    assert sample_frame.session_generation == 2
    assert sample_frame.sequence == 1
    assert sample_frame.kind == "event"
    assert sample_frame.payload == {"event": "sample"}

