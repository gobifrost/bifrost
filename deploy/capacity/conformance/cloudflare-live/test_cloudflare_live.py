"""Pure coverage for the disposable Cloudflare live hook; no cloud calls."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cloudflare_live_hook", HERE / "cloudflare_live.py")
assert SPEC is not None and SPEC.loader is not None
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)

WORKER = "11111111-2222-4333-8444-555555555555"
TOKEN = "s" * 32
ENV = {
    "CF_HOOK_ALLOW_MUTATIONS": "1",
    "CF_CONFORMANCE_WORKER_URL": "https://suite.example.test",
    "CF_CONFORMANCE_SCOPE": "suite-scope",
    "CF_CONFORMANCE_CONTROL_TOKEN": TOKEN,
}


def test_stop_process_posts_exact_scoped_disposable_route() -> None:
    captured = {}

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_request(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    with patch.dict(os.environ, ENV, clear=True), patch.object(hook.urllib.request, "urlopen", open_request):
        assert hook.stop_process({"worker_id": WORKER, "resource_uid": "suite-uid"}) == {"ok": True}

    request = captured["request"]
    assert request.full_url == f"https://suite.example.test/__conformance/v1/workers/{WORKER}/stop"
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert request.get_header("X-bifrost-capacity-scope") == "suite-scope"
    assert json.loads(request.data) == {"resource_uid": "suite-uid"}
    assert captured["timeout"] == 30


def test_stop_process_requires_opt_in_before_network() -> None:
    with patch.dict(os.environ, {key: value for key, value in ENV.items() if key != "CF_HOOK_ALLOW_MUTATIONS"}, clear=True):
        with pytest.raises(hook.Unsupported, match="requires CF_HOOK_ALLOW_MUTATIONS"):
            hook.stop_process({"worker_id": WORKER, "resource_uid": "suite-uid"})


def test_restart_refuses_source_without_the_bundled_wrapper(tmp_path: Path) -> None:
    (tmp_path / "capacity.mjs").write_text("production source", encoding="utf-8")
    (tmp_path / "capacity-core.mjs").write_text("core", encoding="utf-8")
    env = {**ENV, "CF_PROVIDER_SOURCE": str(tmp_path)}
    with patch.dict(os.environ, env, clear=True), patch.object(hook, "_base_hook", side_effect=AssertionError):
        with pytest.raises(hook.Unsupported, match="not the conformance wrapper"):
            hook.restart_provider({"scope": "suite-scope"})


def test_observe_delegates_to_existing_hook() -> None:
    base = SimpleNamespace(observe=lambda payload: {"observed": payload["worker_id"]})
    with patch.object(hook, "_base_hook", return_value=base):
        assert hook.observe({"worker_id": WORKER}) == {"observed": WORKER}


def test_delegated_unsupported_operation_keeps_hook_protocol() -> None:
    class Unsupported(Exception):
        pass

    def unavailable(_payload):
        raise Unsupported("dashboard telemetry is unavailable")

    base = SimpleNamespace(Unsupported=Unsupported, observe=unavailable)
    with patch.object(hook, "_base_hook", return_value=base):
        with pytest.raises(hook.Unsupported, match="dashboard telemetry is unavailable"):
            hook.observe({"worker_id": WORKER})
