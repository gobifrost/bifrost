import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from src.dev import thin_host
from src.dev.thin_host import Bridge, RemoteProcess


@pytest.mark.asyncio
async def test_resource_reports_arrive_before_consumer_initialization():
    bridge = Bridge(Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "thin-1"})
    await bridge.receive({"event": "sample", "current": 2, "limit": 100, "runs": {}})
    assert bridge.memory() == (2, 100)


@pytest.mark.asyncio
async def test_platform_process_wait_survives_heartbeat_wait_cancellation():
    bridge = Bridge(Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "thin-1"})
    process = RemoteProcess(bridge, "route")
    bridge.jobs["route"] = process
    wait = asyncio.create_task(process.wait())
    await asyncio.sleep(0)
    wait.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wait
    assert not process.exited.cancelled()
    await bridge.receive({"event": "job_exit", "route_id": "route", "returncode": 0})
    assert await process.wait() == 0


@pytest.mark.asyncio
async def test_control_reply_resolves_only_its_own_request():
    socket = Mock(send_text=AsyncMock())
    bridge = Bridge(socket, {"current": 1, "limit": 100, "requirements": {}, "hostname": "thin-1"})
    request = asyncio.create_task(bridge.request("ready", {}))
    await asyncio.sleep(0)
    request_id = next(iter(bridge.pending))
    await bridge.receive({"reply": "another-request", "result": {}})
    assert not request.done()
    await bridge.receive({"reply": request_id, "result": {"ready": True}})
    assert await request == {"ready": True}


@pytest.mark.parametrize("authorization", [None, "Bearer ordinary-user-token"])
def test_control_rejects_unauthorized_enrollment(monkeypatch, authorization):
    monkeypatch.setattr(thin_host, "_bootstrap_token", "synthetic-worker-bootstrap")
    monkeypatch.setattr(thin_host, "_enrolled", False)
    application = FastAPI()
    application.add_api_websocket_route("/control", thin_host.thin_control)
    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(application) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("/control", headers=headers):
                raise AssertionError("unauthorized enrollment was accepted")
    assert rejected.value.code == 1008
    assert thin_host._enrolled is False
