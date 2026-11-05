"""Launcher credential protocol: reported at hello, persisted with the owner
generation, and fenced at dispatch (plan §5.4).

A launcher of this build reports protocol 2 and its children receive claims
credentials; a hello without the field is protocol 1, whose children receive
legacy tokens. Credentials minted for one launcher session are never sent to
a replacement session.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.execution_workers import ExecutionWorker
from src.services.execution.process_pool import CredentialRoute
from src.worker.remote import ownership
from src.worker.remote.host import Bridge, RemoteControlHost
from src.worker.remote.identity import LauncherIdentity
from src.worker.remote.launcher import Launcher
from src.worker.remote.ownership import RemoteOwnerLease
from src.worker.remote.protocol import (
    CREDENTIAL_PROTOCOL,
    CredentialRouteChanged,
    Hello,
    hello_credential_protocol,
    sign_hello,
)
from src.worker.remote.remote_pool import ThinRemoteProcessPool

LEASE = RemoteOwnerLease(
    worker_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    owner_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    launcher_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    session_generation=4,
    credential_protocol=2,
)


@pytest.mark.parametrize(
    ("frame", "protocol"),
    [
        ({}, 1),
        ({"credential_protocol": 2}, 2),
        ({"credential_protocol": True}, 1),
        ({"credential_protocol": "2"}, 1),
        ({"credential_protocol": 0}, 1),
    ],
    ids=["absent", "two", "bool", "string", "zero"],
)
def test_a_hello_without_a_valid_field_is_protocol_one(frame: dict[str, Any], protocol: int) -> None:
    assert hello_credential_protocol(frame) == protocol


async def test_a_launcher_of_this_build_reports_the_claims_protocol() -> None:
    identity = LauncherIdentity(
        launcher_id=uuid4(), incarnation_id=uuid4(), job_uid=None, pod_uid=None, hostname="runner", form="static",
    )
    socket = AsyncMock()
    socket.recv.return_value = json.dumps({"event": "welcome", "session_generation": 1})
    launcher = Launcher(socket, identity=identity, secret="secret")

    await launcher.hello({})

    sent = json.loads(socket.send.await_args_list[0].args[0])
    assert sent["credential_protocol"] == CREDENTIAL_PROTOCOL == 2


@pytest.mark.parametrize(("reported", "persisted"), [(2, 2), (None, 1)], ids=["reported", "absent"])
async def test_the_host_hands_the_reported_protocol_to_the_owner_generation(
    reported: int | None, persisted: int
) -> None:
    socket = Mock(accept=AsyncMock(), send_text=AsyncMock())
    hello = Hello(
        launcher_id=LEASE.launcher_id,
        incarnation_id=LEASE.worker_id,
        job_uid="job", pod_uid="pod", hostname="runner-0", form="job",
        issued_at=int(time.time()), nonce="n" * 32, hello_sequence=1,
    )
    initial: dict[str, Any] = {
        "event": "hello", "hello": sign_hello("secret", hello).to_dict(),
        "sample": {"current": 1, "limit": 100}, "requirements": {},
    }
    if reported is not None:
        initial["credential_protocol"] = reported
    socket.receive_text = AsyncMock(return_value=json.dumps(initial))
    with (
        patch("src.worker.remote.host.get_settings", return_value=Mock(secret_key="secret", memory_pressure_threshold=.85)),
        patch("src.worker.remote.host.admit_worker", new_callable=AsyncMock),
        patch("src.worker.remote.host.acquire_remote_owner", new_callable=AsyncMock, return_value=LEASE) as acquire,
        patch("src.worker.remote.host.ApiExecutor"),
        patch("src.worker.remote.host.RemoteSession", return_value=Mock(task=Mock())),
    ):
        await RemoteControlHost().connect(socket)

    assert acquire.await_args.kwargs["credential_protocol"] == persisted


async def test_the_protocol_is_persisted_with_each_owner_generation(async_session_factory, monkeypatch) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(ownership, "get_liveness_db_context", context)
    worker_id = uuid4()
    async with async_session_factory() as session:
        session.add(ExecutionWorker(id=worker_id, form="job", state="ready", resource_class="standard", slots=1))
        await session.commit()
    owner, launcher = uuid4(), uuid4()
    try:
        first = await ownership.acquire_remote_owner(
            worker_id=worker_id, owner_id=owner, launcher_id=launcher,
            nonce_digest="a" * 64, hello_sequence=1, credential_protocol=2,
        )
        reconnected = await ownership.acquire_remote_owner(
            worker_id=worker_id, owner_id=owner, launcher_id=launcher,
            nonce_digest="b" * 64, hello_sequence=2,
        )
        async with async_session_factory() as session:
            stored = await session.scalar(
                select(ExecutionWorker.remote_credential_protocol).where(ExecutionWorker.id == worker_id)
            )
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await session.commit()

    assert first is not None and first.credential_protocol == 2
    assert reconnected is not None
    assert (reconnected.session_generation, reconnected.credential_protocol) == (first.session_generation + 1, 1)
    assert stored == 1


class _Transport:
    connected = True
    hostname = "thin-1"

    def __init__(self, lease: RemoteOwnerLease) -> None:
        self.lease = lease
        self.generations: list[int | None] = []

    async def request(
        self, action: str, payload: dict[str, Any], *, expected_generation: int | None = None
    ) -> dict[str, Any]:
        self.generations.append(expected_generation)
        return {"accepted": True, "pid": 12345}

    def memory_request_bytes(self, key: str) -> int:
        return 1024

    def memory(self) -> tuple[int, int]:
        return (600, 1000)


async def test_a_remote_route_names_the_launcher_protocol_and_dispatch_is_fenced_to_its_session() -> None:
    legacy = replace(LEASE, credential_protocol=1)
    transport = _Transport(legacy)
    pool = ThinRemoteProcessPool(transport, on_result=AsyncMock())

    route = pool.credential_route()
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        active = {"execution_id": "exec-1", "workflow_id": None, "workflow_name": "w", "org_id": None,
                  "user_id": None, "user_name": None, "user_email": None, "sync": False, "event": None}
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, active, credential_route=route)  # type: ignore[arg-type]
        await pool.route_service(
            service_id="svc", attempt_id="att", lease_token="lease", context={}, credential_route=route
        )

    assert route == CredentialRoute(1, LEASE.session_generation)
    assert transport.generations == [LEASE.session_generation, LEASE.session_generation]


async def test_credentials_minted_for_a_replaced_session_are_never_sent() -> None:
    socket = Mock(send_text=AsyncMock())
    bridge = Bridge(socket, {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE)
    bridge.lease = replace(LEASE, session_generation=LEASE.session_generation + 1, credential_protocol=1)

    with pytest.raises(CredentialRouteChanged):
        await bridge.request("workflow", {"context": {"engine_token": "claims"}}, expected_generation=LEASE.session_generation)

    socket.send_text.assert_not_awaited()
    assert bridge.connected is True
    assert bridge.pending == {}
