"""Pure tests for the local Cloudflare normal-image suite wrapper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cloudflare_live_suite", HERE / "cloudflare_live_suite.py")
assert SPEC is not None and SPEC.loader is not None
suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(suite)

WORKER = "11111111-2222-4333-8444-555555555555"
UID = "suite-resource"
RESERVATION = {
    "worker_id": WORKER,
    "worker_version": "build-1",
    "memory_limit_bytes": 4 * 1024**3,
    "max_lifetime_seconds": 3600,
    "drain_grace_seconds": 300,
}


def test_bridge_uses_docker_exec_stdin_without_shell_or_token_arguments() -> None:
    seen = {}

    def run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True}), stderr="")

    with patch.object(suite.subprocess, "run", run):
        suite.ApiContainerBridge("test-api").seed({"scope": "suite", "worker_id": WORKER})

    assert seen["args"] == ["docker", "exec", "-i", "test-api", "python", suite.BRIDGE_PATH]
    assert seen["kwargs"]["input"] == json.dumps({"action": "seed", "scope": "suite", "worker_id": WORKER}, separators=(",", ":"))
    assert seen["kwargs"]["shell"] is False
    assert "token" not in seen["kwargs"]["input"]


def test_image_client_seeds_only_after_fresh_exact_prepared_readback_and_waits_for_admission() -> None:
    calls = []

    class BaseClient:
        def __init__(self, _base, _token, scope):
            self.scope = scope

        def put(self, worker_id, _reservation):
            return 200, {"worker_id": worker_id, "resource_uid": UID, "state": "prepared"}

        def get(self, worker_id):
            return 200, {"worker_id": worker_id, "scope": "suite", "resource_uid": UID, "state": "prepared", "worker_version": "build-1"}

        def start(self, worker_id, uid):
            calls.append((worker_id, uid))
            return 200, {"state": "starting"}

    bridge = SimpleNamespace(seed=lambda payload: calls.append(("seed", payload)),
                             wait_ready=lambda payload: calls.append(("ready", payload)))
    client = suite.image_client(BaseClient, bridge)("https://capacity", "token", "suite")
    client.put(WORKER, RESERVATION)

    assert client.start(WORKER, UID)[0] == 200
    assert [call[0] for call in calls] == ["seed", WORKER, "ready"]
    assert calls[0][1]["reservation"] == RESERVATION


def test_image_client_never_seeds_wrong_uid_or_replay() -> None:
    calls = []

    class BaseClient:
        def __init__(self, _base, _token, scope):
            self.scope = scope

        def get(self, worker_id):
            return 200, {"worker_id": worker_id, "scope": "suite", "resource_uid": UID, "state": "starting", "worker_version": "build-1"}

        def start(self, worker_id, uid):
            calls.append((worker_id, uid))
            return 200, {"state": "starting"}

    bridge = SimpleNamespace(seed=lambda _payload: pytest.fail("must not seed"),
                             wait_ready=lambda _payload: pytest.fail("must not wait"))
    client = suite.image_client(BaseClient, bridge)("https://capacity", "token", "suite")
    client._prepared[WORKER] = RESERVATION

    client.start(WORKER, "wrong-uid")
    client.start(WORKER, UID)
    assert calls == [(WORKER, "wrong-uid"), (WORKER, UID)]


def test_bridge_waits_until_the_api_reports_bound_ready_admission() -> None:
    bridge = suite.ApiContainerBridge("test-api", ready_timeout_seconds=2)
    calls = []

    def call(payload):
        calls.append(payload)
        return {"ok": True, "ready": len(calls) == 2}

    with patch.object(bridge, "_call", call), patch.object(suite.time, "sleep"):
        bridge.wait_ready({"scope": "suite", "worker_id": WORKER})

    assert calls == [
        {"action": "ready", "scope": "suite", "worker_id": WORKER},
        {"action": "ready", "scope": "suite", "worker_id": WORKER},
    ]
