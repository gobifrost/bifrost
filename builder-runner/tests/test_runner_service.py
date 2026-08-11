import json
import threading
import time
import uuid
from http.client import HTTPConnection
from pathlib import Path

import pytest

import runner
import runner_service
from runner_service import RunnerManager, RunnerServiceServer


class FakeRunResult:
    def __init__(self, payload: dict[str, object], return_code: int = 0) -> None:
        self.stdout = json.dumps(payload)
        self.stderr = ""
        self.returncode = return_code


class FakeProcess:
    def __init__(self, event: threading.Event) -> None:
        self._event = event
        self._return_code = 0
        self.pid = 1234

    def wait(self, timeout: float | None = None) -> int:
        self._event.wait(timeout=timeout)
        return self._return_code

    def poll(self) -> int | None:
        return self._return_code if self._event.is_set() else None

    def terminate(self) -> None:
        self._return_code = runner.REPORTED_CANCELLED_EXIT
        self._event.set()


class FakeEnvironment:
    def __init__(self) -> None:
        self.popen_calls: list[list[str]] = []
        self.popen_called = threading.Event()
        self.run_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.run_events: dict[str, threading.Event] = {}

    def popen(self, command: list[str], **kwargs: object) -> FakeProcess:
        assert command[0] == runner_service.RUNNER_PATH
        assert len(command) == 2
        envelope_path = Path(command[1])
        assert envelope_path.name == "job.json"
        run_id = envelope_path.parent.name
        event = self.run_events.setdefault(run_id, threading.Event())
        self.popen_calls.append(command)
        self.popen_called.set()
        return FakeProcess(event)

    def run(self, *args: object, **kwargs: object) -> FakeRunResult:
        self.run_calls.append((args, kwargs))
        return FakeRunResult(
            {
                "ready": True,
                "schema_version": runner.SCHEMA_VERSION,
                "harness": "opencode",
            }
        )


def run_payload() -> dict:
    return {
        "instance_id": "instance-a",
        "job": {
            "schema_version": 1,
            "job_id": str(uuid.uuid4()),
            "job_type": "solution.build",
            "dispatch_attempt": 1,
            "callback_base_url": "https://example.internal",
            "capability": "cap",
            "input_sha256": "0" * 64,
            "timeout_seconds": 60,
        },
    }


def request(
    port: int,
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
    body: bytes | None = None,
    secret: str = "top-secret",
) -> tuple[int, dict]:
    if body is None and payload is not None:
        body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {secret}",
    }
    connection = HTTPConnection("127.0.0.1", port)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    response.close()
    try:
        return response.status, json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return response.status, {}


@pytest.fixture
def fake_env() -> FakeEnvironment:
    return FakeEnvironment()


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_env: FakeEnvironment):
    manager = RunnerManager(
        secret="top-secret",
        workdir=tmp_path,
        max_concurrent=1,
    )
    monkeypatch.setattr(runner_service.subprocess, "Popen", fake_env.popen)
    monkeypatch.setattr(runner_service.subprocess, "run", fake_env.run)
    server = RunnerServiceServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    try:
        while port == 0:
            time.sleep(0.001)
            port = server.server_port
        yield server, manager, fake_env, port
    finally:
        server.shutdown()
        server.server_close()


def test_health_returns_service_status(service):
    server, _manager, fake, port = service
    status, body = request(port, "GET", "/health")
    assert status == 200
    assert body == {
        "max_concurrent": 1,
        "running": 0,
        "status": "ok",
    }


def test_auth_is_required(service):
    _server, _manager, fake, port = service
    payload = {"x": "y"}
    status, body = request(port, "GET", "/health", payload=payload, secret="")
    assert status == 401


def test_provision_runs_probe(service):
    server, _manager, fake, port = service
    status, body = request(port, "POST", "/provision")
    assert status == 200
    assert body["ready"] is True
    assert body["schema_version"] == runner.SCHEMA_VERSION
    assert fake.run_calls


def test_post_jobs_is_idempotent(service):
    server, _manager, fake, port = service
    payload = run_payload()

    first_status, first_body = request(port, "POST", "/jobs", payload=payload)
    second_status, second_body = request(port, "POST", "/jobs", payload=payload)

    assert first_status == 202
    assert second_status == 200
    assert first_body["run_id"] == second_body["run_id"]
    assert fake.popen_called.wait(timeout=1)
    assert len(fake.popen_calls) == 1


def test_post_jobs_persists_envelope_and_runs_with_fixed_command(service, tmp_path: Path):
    server, _manager, fake, port = service
    payload = run_payload()
    status, body = request(port, "POST", "/jobs", payload=payload)

    assert status == 202
    run_id = body["run_id"]
    run_dir = tmp_path / "jobs" / "instance-a" / run_id
    assert (run_dir / "job.json").is_file()
    assert (run_dir / "state.json").is_file()
    assert fake.popen_called.wait(timeout=1)
    command = fake.popen_calls[0]
    assert command == [runner_service.RUNNER_PATH, str(run_dir / "job.json")]


def test_post_jobs_rejects_invalid_instance_id(service):
    server, _manager, fake, port = service
    payload = run_payload()
    payload["instance_id"] = "../bad-id"
    status, body = request(port, "POST", "/jobs", payload=payload)
    assert status == 400
    assert "instance_id" in body["error"]


def test_post_jobs_rejects_malformed_body(service):
    server, _manager, fake, port = service
    status, body = request(port, "POST", "/jobs", payload={"instance_id": "instance-a"})
    assert status == 400
    assert body["error"]


def test_post_jobs_rejects_oversized_body(service):
    server, _manager, fake, port = service
    payload = {
        "instance_id": "instance-a",
        "job": {"x": "y"},
    }
    body = json.dumps(payload).encode("utf-8") + b"x" * (runner_service.MAX_BODY_BYTES + 1 - len(payload))
    status, _body = request(port, "POST", "/jobs", body=body)
    assert status == 413


def test_post_jobs_respects_concurrency_limit(service, monkeypatch):
    server, manager, fake, port = service
    manager.max_concurrent = 1

    first, _ = request(port, "POST", "/jobs", payload=run_payload())
    second_payload = run_payload()
    second, body = request(port, "POST", "/jobs", payload=second_payload)
    assert first == 202
    assert second == 429
    assert body["error"]


def test_delete_terminates_running_job(service):
    server, _manager, fake, port = service
    status, body = request(port, "POST", "/jobs", payload=run_payload())
    run_id = body["run_id"]
    assert status == 202

    stop_status, stop_body = request(port, "DELETE", f"/jobs/{run_id}")
    assert stop_status == 200
    assert stop_body["status"] == "cancelled"


def test_get_jobs_returns_run_state(service):
    server, _manager, fake, port = service
    status, body = request(port, "POST", "/jobs", payload=run_payload())
    assert status == 202
    run_id = body["run_id"]

    inspect_status, inspect_body = request(port, "GET", f"/jobs/{run_id}")
    assert inspect_status == 200
    assert inspect_body["run_id"] == run_id
    assert inspect_body["status"] in {"queued", "running"}


def test_delete_unknown_run_id(service):
    server, _manager, fake, port = service
    status, body = request(port, "DELETE", f"/jobs/{uuid.uuid4()}")
    assert status == 404
    assert body["error"] == "run_id not found"


def test_delete_rejects_invalid_run_id(service):
    server, _manager, fake, port = service
    status, body = request(port, "DELETE", "/jobs/not-a-uuid")
    assert status == 400
    assert body["error"] == "run_id is not valid"


def test_service_reports_probe_error(service, monkeypatch):
    server, _manager, fake, port = service
    monkeypatch.setattr(
        runner_service.subprocess,
        "run",
        lambda *_args, **_kwargs: FakeRunResult({"harness": "python"}, return_code=1),
    )
    status, body = request(port, "POST", "/provision")
    assert status == 503
    assert "harness" in body["error"]


def test_delete_accepts_querystring(service):
    server, _manager, fake, port = service
    status, body = request(port, "POST", "/jobs", payload=run_payload())
    run_id = body["run_id"]
    assert status == 202

    stop_status, stop_body = request(port, "DELETE", f"/jobs/{run_id}?force=true")
    assert stop_status == 200
    assert stop_body["run_id"] == run_id
    assert stop_body["status"] == "cancelled"
