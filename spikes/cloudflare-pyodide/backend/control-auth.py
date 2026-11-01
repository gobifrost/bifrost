"""Live isolated control handshake only; no inventory, ready or execution."""
import asyncio
import json
import os
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import websockets
from websockets.exceptions import ConnectionClosed


async def main():
    target = urlsplit(os.environ.get("BIFROST_DATABASE_URL", ""))
    if (os.environ.get("BIFROST_ENVIRONMENT") != "testing"
        or os.environ.get("TEST_API_URL") != "http://api:8000"
        or target.hostname != "pgbouncer" or target.path != "/bifrost_test"):
        raise RuntimeError("Own disposable test stack required")
    from sqlalchemy import delete, func, select
    from src.core.database import init_db, close_db, get_db_context
    from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
    from src.services.worker_pool.claims import register_static_worker
    from src.services.execution.memory_monitor import effective_memory_sample
    from src.worker.remote.identity import LauncherIdentity
    from src.worker.remote.launcher import Launcher
    from src.worker.remote.protocol import ControlEnvelope
    from shared.version import get_version

    await init_db()
    worker_id = await register_static_worker(hostname="pyodide-control-auth-fixture",
        incarnation_id=uuid4(), supported_handlers=[], version=get_version())
    sockets = []
    report = {"runtime": "live-isolated-upstream-control-auth", "control_auth_probe": True,
              "cloud_invocations": 0, "actual_worker_invocations": 0,
              "physical_termination_verified": False, "inventory_sent": False,
              "execution_admission_qualified": False}

    async def snapshot():
        async with get_db_context() as db:
            row = await db.get(ExecutionWorker, worker_id)
            assert row is not None and row.supported_handlers == []
            assert row.state == "starting" and row.ready_at is None
            claims = await db.scalar(select(func.count()).select_from(ExecutionWorkerClaim).where(
                ExecutionWorkerClaim.worker_id == worker_id))
            assert claims == 0
            return row.remote_session_generation, row.remote_owner_id

    async def connect():
        socket = await websockets.connect("ws://api:8000/api/worker-control",
            open_timeout=10, close_timeout=10, max_size=1048576)
        sockets.append(socket)
        return socket

    class RecordingSocket:
        hello = None

        def __init__(self, socket, *, corrupt=False):
            self.socket, self.corrupt = socket, corrupt

        async def send(self, raw):
            frame = json.loads(raw)
            assert frame["event"] == "hello"  # No inventory or runtime commands.
            if self.corrupt:
                proof = frame["hello"]["proof"]
                frame["hello"]["proof"] = ("0" if proof[0] != "0" else "1") + proof[1:]
                raw = json.dumps(frame)
            self.hello = raw  # Memory only: never print or persist signed hello.
            await self.socket.send(raw)

        async def recv(self):
            return await self.socket.recv()

    async def refused(receive):
        try:
            await receive
        except ConnectionClosed as exc:
            assert exc.rcvd is not None and exc.rcvd.code == 1008
            return exc.rcvd.code
        raise AssertionError("Unsafe control message was accepted")

    try:
        before = await snapshot()
        identity = LauncherIdentity(uuid4(), worker_id, None, None, "pyodide-control-auth-fixture", "static")
        invalid = RecordingSocket(await connect(), corrupt=True)
        launcher = Launcher(invalid, identity=identity, secret=os.environ["BIFROST_SECRET_KEY"])
        report["invalid_signature_close"] = await refused(launcher._hello({}))
        assert await snapshot() == before
        report["invalid_signature_did_not_acquire_owner"] = True

        first = RecordingSocket(await connect())
        await launcher.reconnect(first, {})
        generation, owner = await snapshot()
        assert generation == launcher.session_generation and generation >= 1 and owner is not None
        report["signed_hello_generation"] = generation

        replay = await connect()
        await replay.send(first.hello)
        report["nonce_replay_close"] = await refused(replay.recv())
        assert await snapshot() == (generation, owner)
        report["nonce_replay_did_not_advance_owner"] = True

        second = RecordingSocket(await connect())
        await launcher.reconnect(second, {})
        current, same_owner = await snapshot()
        assert current > generation and current == launcher.session_generation and same_owner == owner
        report["signed_reconnect_generation"] = current
        parent_current, parent_limit = effective_memory_sample()
        old = ControlEnvelope(session_generation=generation, sequence=1, kind="event",
            payload={"event": "sample", "current": parent_current, "limit": parent_limit}).to_json()
        await first.socket.send(old)
        report["superseded_socket_close"] = await refused(first.socket.recv())
        assert await snapshot() == (current, owner)
        await second.socket.send(old)
        report["stale_generation_close"] = await refused(second.socket.recv())
        assert await snapshot() == (current, owner)
        assert launcher.template.pid is None and launcher.agent_template.pid is None
        report.update(empty_handlers_preserved=True, remained_starting=True, claims_observed=0,
                      linux_templates_unstarted=True, signed_hello_material_retained=False)
    except Exception:
        print(json.dumps({**report, "qualification_failed": True}))
        raise
    finally:
        await asyncio.gather(*(socket.close() for socket in sockets))
        async with get_db_context() as db:
            await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
        await close_db()
    await init_db()
    try:
        async with get_db_context() as db:
            assert await db.get(ExecutionWorker, worker_id) is None
        report["fixture_worker_cleanup_verified"] = True
    finally:
        await close_db()
    print(json.dumps(report))


if __name__ == "__main__":
    assert sys.argv[1:] == ["--control-auth"]
    try:
        asyncio.run(main())
    except Exception as exc:
        print(json.dumps({"runtime": "live-isolated-upstream-control-auth", "error_type": type(exc).__name__}))
        raise SystemExit(1)
