from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from urllib.error import URLError

import pytest

import runner


def envelope(**overrides: object) -> runner.Envelope:
    data = {
        "schema_version": 1,
        "job_id": "11111111-1111-4111-8111-111111111111",
        "job_type": "solution.build",
        "dispatch_attempt": 1,
        "callback_base_url": "https://bifrost.example.com",
        "capability": "capability",
        "input_sha256": "0" * 64,
        "timeout_seconds": 60,
    }
    data.update(overrides)
    return runner.Envelope.parse(json.dumps(data).encode())


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


class FakeClient:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.envelope = envelope(job_type="solution.builder.turn")
        self.base = "https://bifrost.example.com/api/internal/sandbox/jobs/job"
        self.progress_calls: list[tuple[str, int, int | None]] = []

    def ensure_not_cancelled(self) -> None:
        if self.cancelled:
            raise runner.Cancelled("job cancelled")

    def progress(self, phase: str, current: int = 0, total: int | None = None) -> None:
        self.progress_calls.append((phase, current, total))


def test_envelope_rejects_unknown_fields() -> None:
    raw = json.dumps({**envelope().__dict__, "provider_api_key": "secret"}).encode()
    with pytest.raises(runner.RunnerError, match="unknown envelope fields"):
        runner.Envelope.parse(raw)


def test_envelope_rejects_callback_credentials() -> None:
    with pytest.raises(runner.RunnerError, match="cannot contain credentials"):
        envelope(callback_base_url="https://user:pass@example.com")


def test_callback_client_uses_explicit_api_user_agent() -> None:
    client = runner.CallbackClient(envelope())

    assert client.headers == {
        "Authorization": "Bearer capability",
        "User-Agent": "Bifrost-Builder-Runner/1.0",
    }


def test_callback_client_retries_transient_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok":true}'

    responses: list[object] = [URLError("temporarily unavailable"), Response()]
    sleeps: list[float] = []

    def request(*_args: object, **_kwargs: object):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(runner, "urlopen", request)
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)

    body = runner.CallbackClient(envelope()).request("GET", "/context")

    assert body == b'{"ok":true}'
    assert sleeps == [1]


def test_cancellation_check_tolerates_transient_network_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = runner.CallbackClient(envelope())

    def unavailable(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise runner.CallbackRequestError("callback timed out", retryable=True)

    monkeypatch.setattr(client, "get_json", unavailable)
    for _ in range(121):
        client.ensure_not_cancelled()

    assert client._cancel_check_failures == 121


def test_cancellation_check_rejects_non_retryable_callback_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = runner.CallbackClient(envelope())

    def unauthorized(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise runner.CallbackRequestError("callback failed: 401", retryable=False)

    monkeypatch.setattr(client, "get_json", unauthorized)

    with pytest.raises(runner.CallbackRequestError, match="401"):
        client.ensure_not_cancelled()


def test_extract_zip_rejects_path_escape(tmp_path: Path) -> None:
    archive = tmp_path / "input.zip"
    archive.write_bytes(zip_bytes({"../evil.txt": b"no"}))
    with pytest.raises(runner.RunnerError, match="unsafe archive member"):
        runner.extract_zip(archive, tmp_path / "out", FakeClient())


def test_extract_zip_observes_cancellation(tmp_path: Path) -> None:
    archive = tmp_path / "input.zip"
    archive.write_bytes(zip_bytes({"package.json": b"{}"}))
    with pytest.raises(runner.Cancelled):
        runner.extract_zip(archive, tmp_path / "out", FakeClient(cancelled=True))


def test_runner_probe_and_file_envelope(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda command: f"/usr/bin/{command}")
    assert runner.main(["--probe"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "ready": True,
        "schema_version": 1,
        "harness": "opencode",
    }

    path = tmp_path / "envelope.json"
    path.write_text(json.dumps(envelope().__dict__), encoding="utf-8")
    parsed: list[runner.Envelope] = []
    monkeypatch.setenv("BIFROST_RUNNER_WORKDIR", str(tmp_path / "work"))
    monkeypatch.setattr(runner, "run", lambda value, _root: parsed.append(value) or 0)
    assert runner.main([str(path)]) == 0
    assert parsed == [envelope()]


@pytest.mark.parametrize("base", ["/solution/apps/app/", "./"])
def test_build_commands_use_fixed_bifrost_toolchain(tmp_path: Path, base: str) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"private": True, "scripts": {"build": "malicious-command"}}),
        encoding="utf-8",
    )
    (tmp_path / "build-meta.json").write_text(
        json.dumps({"base": base}),
        encoding="utf-8",
    )
    install, build = runner.build_commands(tmp_path)
    assert install == ("npm", "install", "--ignore-scripts", "--no-audit", "--no-fund")
    assert build[:5] == ("npx", "--no-install", "vite", "build", "--config")
    assert build[-1] == base
    assert "malicious-command" not in build


@pytest.mark.parametrize("base", ["https://evil.example", "//evil.example", "/ok?x=1"])
def test_build_commands_reject_non_path_base(tmp_path: Path, base: str) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"private": True}),
        encoding="utf-8",
    )
    (tmp_path / "build-meta.json").write_text(
        json.dumps({"base": base}),
        encoding="utf-8",
    )
    with pytest.raises(runner.RunnerError, match="invalid base path"):
        runner.build_commands(tmp_path)


def test_opencode_config_uses_job_scoped_gateway_and_compaction() -> None:
    client = FakeClient()
    config = runner.opencode_config(
        client,
        {
            "model": "cheap-builder-model",
            "system_prompt": "Build complete solutions.",
            "bundle_path": "skills/bifrost-build",
            "max_iterations": 40,
            "messages": [
                {"role": "user", "content": "Earlier request"},
                {"role": "assistant", "content": "Earlier response"},
                {"role": "user", "content": "Build an app"},
            ],
        },
    )

    provider = config["provider"]["bifrost"]
    assert provider["options"]["baseURL"].endswith("/llm/v1")
    assert provider["options"]["apiKey"] == "capability"
    assert provider["options"]["timeout"] == 15 * 60 * 1000
    assert config["compaction"] == {"auto": True, "prune": True}
    assert config["agent"]["bifrost-builder"]["steps"] == 40
    assert "Earlier request" in config["agent"]["bifrost-builder"]["prompt"]
    assert "skills/bifrost-build/SKILL.md" in config["agent"]["bifrost-builder"]["prompt"]
    prompt = config["agent"]["bifrost-builder"]["prompt"]
    assert prompt.index("Build complete solutions.") < prompt.index(
        "## Native Builder Runtime Contract"
    )
    assert "Do not run CLI authentication/environment probes" in prompt
    assert "Never start a development server" in prompt
    assert config["agent"]["bifrost-builder"]["permission"]["doom_loop"] == "deny"
    bash_permissions = config["agent"]["bifrost-builder"]["permission"]["bash"]
    assert bash_permissions["*"] == "allow"
    assert bash_permissions["*npm run dev*"] == "deny"
    assert bash_permissions["*--watch*"] == "deny"


def test_restored_opencode_config_does_not_reinject_conversation_history() -> None:
    config = runner.opencode_config(
        FakeClient(),
        {
            "model": "cheap-builder-model",
            "system_prompt": "Build complete solutions.",
            "max_iterations": 40,
            "messages": [
                {"role": "user", "content": "Earlier request"},
                {"role": "assistant", "content": "Earlier response"},
                {"role": "user", "content": "Build an app"},
            ],
        },
        restored_session=True,
    )

    prompt = config["agent"]["bifrost-builder"]["prompt"]
    assert "Earlier request" not in prompt
    assert "Build complete solutions." in prompt


def test_harness_state_round_trips_session_and_runtime_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".local" / "share" / "opencode"
    state.mkdir(parents=True)
    (state / "opencode.db").write_bytes(b"database")
    archive = tmp_path / "state.zip"

    digest = runner.harness_state_zip(home, archive, "ses_123")
    restored_home = tmp_path / "restored"
    restored_home.mkdir()
    session_id = runner.restore_harness_state(archive, restored_home, FakeClient())

    assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert session_id == "ses_123"
    assert (
        restored_home / ".local" / "share" / "opencode" / "opencode.db"
    ).read_bytes() == b"database"
    assert not (restored_home / "harness.json").exists()


def test_cloudflare_sdk_and_runner_base_image_versions_match() -> None:
    root = Path(__file__).resolve().parents[2]
    package = json.loads(
        (root / "api/src/services/cloudflare_runner/package.json").read_text(
            encoding="utf-8"
        )
    )
    dockerfile = (root / "builder-runner/Dockerfile").read_text(encoding="utf-8")
    version = package["dependencies"]["@cloudflare/sandbox"]
    assert f"FROM docker.io/cloudflare/sandbox:{version}-opencode" in dockerfile
    assert "bifrost-sandbox-runner" in dockerfile


def test_workspace_zip_is_deterministic(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "b.txt").write_text("b", encoding="utf-8")
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    one = tmp_path / "one.zip"
    two = tmp_path / "two.zip"
    digest_one = runner.workspace_zip(workspace, one)
    digest_two = runner.workspace_zip(workspace, two)
    assert digest_one == digest_two
    assert hashlib.sha256(one.read_bytes()).hexdigest() == digest_one
    assert one.read_bytes() == two.read_bytes()


def test_opencode_exit_uses_helper_diagnostic() -> None:
    error = runner._opencode_exit_error(
        1,
        b"OpenCode prompt failed: Provider error: invalid tool result\n",
    )

    assert error.message == (
        "OpenCode turn failed: OpenCode prompt failed: "
        "Provider error: invalid tool result"
    )
    assert error.log_excerpt == (
        "OpenCode prompt failed: Provider error: invalid tool result"
    )


def test_opencode_exit_extracts_privacy_safe_structured_diagnostics() -> None:
    raw = json.dumps(
        {
            "error": "Provider rejected a tool response",
            "harness_session_id": "ses_123",
            "harness_diagnostics": {
                "message_count": 4,
                "assistant_message_count": 2,
                "tool_call_count": 3,
                "tool_error_count": 1,
                "other_tool_call_count": 0,
                "compaction_count": 0,
                "retry_count": 0,
                "truncated": False,
                "tools": [{"name": "write", "count": 3, "error_count": 1}],
            },
        }
    ).encode()

    error = runner._opencode_exit_error(1, raw)

    assert error.message == "OpenCode turn failed: Provider rejected a tool response"
    assert error.harness_session_id == "ses_123"
    assert error.harness_diagnostics is not None
    assert error.harness_diagnostics["tool_call_count"] == 3


def test_session_marker_is_strict_and_recovers_session_id(tmp_path: Path) -> None:
    marker = tmp_path / "session.json"
    marker.write_text(
        json.dumps({"schema_version": 1, "session_id": "ses_123"}),
        encoding="utf-8",
    )
    assert runner._session_id_from_marker(marker) == "ses_123"

    marker.write_text(
        json.dumps(
            {"schema_version": 1, "session_id": "ses_123", "prompt": "private"}
        ),
        encoding="utf-8",
    )
    assert runner._session_id_from_marker(marker) is None


def test_run_reports_build_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Client:
        def __init__(self, _envelope: runner.Envelope) -> None:
            self.completed: dict[str, object] | None = None

        def progress(self, *_args: object, **_kwargs: object) -> None:
            pass

        def complete_build(self, status: str, **kwargs: object) -> None:
            self.completed = {"status": status, **kwargs}

    client = Client(envelope())
    monkeypatch.setattr(runner, "CallbackClient", lambda _envelope: client)
    monkeypatch.setattr(runner, "download_input", lambda *_args, **_kwargs: None)

    def fail_extract(*_args: object, **_kwargs: object) -> None:
        raise runner.RunnerError("bad input")

    monkeypatch.setattr(runner, "extract_zip", fail_extract)
    code = runner.run(envelope(), tmp_path)
    assert code == 1
    assert client.completed is not None
    assert client.completed["status"] == "failed"
    assert client.completed["error"] == "bad input"


def test_run_reports_turn_cancellation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Client:
        def __init__(self, _envelope: runner.Envelope) -> None:
            self.completed: dict[str, object] | None = None

        def progress(self, *_args: object, **_kwargs: object) -> None:
            pass

        def get_json(self, _path: str) -> dict[str, object]:
            return {}

        def complete_turn(self, body: dict[str, object]) -> None:
            self.completed = body

    turn_envelope = envelope(job_type="solution.builder.turn")
    client = Client(turn_envelope)
    monkeypatch.setattr(runner, "CallbackClient", lambda _envelope: client)
    monkeypatch.setattr(runner, "download_input", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "extract_zip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "download_harness_state",
        lambda *_args, **_kwargs: False,
    )

    def cancel(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise runner.Cancelled("job cancelled")

    monkeypatch.setattr(runner, "run_opencode_turn", cancel)
    code = runner.run(turn_envelope, tmp_path)
    assert code == 2
    assert client.completed == {"status": "cancelled", "error": "job cancelled"}


def test_run_checkpoints_workspace_and_harness_after_turn_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Client:
        def __init__(self, _envelope: runner.Envelope) -> None:
            self.completed: dict[str, object] | None = None
            self.uploads: dict[str, bytes] = {}

        def progress(self, *_args: object, **_kwargs: object) -> None:
            pass

        def get_json(self, _path: str) -> dict[str, object]:
            return {}

        def request(
            self,
            method: str,
            path: str,
            *,
            body: bytes | None = None,
            **_kwargs: object,
        ) -> bytes:
            assert method == "PUT"
            assert body is not None
            self.uploads[path] = body
            return b'{}'

        def complete_turn(self, body: dict[str, object]) -> None:
            self.completed = body

    turn_envelope = envelope(job_type="solution.builder.turn")
    client = Client(turn_envelope)
    monkeypatch.setattr(runner, "CallbackClient", lambda _envelope: client)
    monkeypatch.setattr(runner, "download_input", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "extract_zip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "download_harness_state",
        lambda *_args, **_kwargs: False,
    )

    def cancel(
        _client: object,
        workspace: Path,
        _context: dict[str, object],
        _timeout: int,
        *,
        home: Path,
        restored_session_id: str | None,
    ) -> dict[str, object]:
        assert restored_session_id is None
        (workspace / "partial.tsx").write_text("partial", encoding="utf-8")
        state = home / ".local" / "share" / "opencode"
        state.mkdir(parents=True)
        (state / "opencode.db").write_bytes(b"state")
        raise runner.Cancelled(
            "job cancelled",
            harness_session_id="ses_checkpoint",
        )

    monkeypatch.setattr(runner, "run_opencode_turn", cancel)

    assert runner.run(turn_envelope, tmp_path) == runner.REPORTED_CANCELLED_EXIT
    assert set(client.uploads) == {"/harness-state", "/output"}
    assert client.completed is not None
    assert client.completed["status"] == "cancelled"
    assert client.completed["checkpoint_output_sha256"] == hashlib.sha256(
        client.uploads["/output"]
    ).hexdigest()


def test_run_distinguishes_terminal_callback_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Client:
        def __init__(self, _envelope: runner.Envelope) -> None:
            pass

        def progress(self, *_args: object, **_kwargs: object) -> None:
            pass

        def complete_build(self, _status: str, **_kwargs: object) -> None:
            raise runner.RunnerError("callback POST /complete failed: unavailable")

    monkeypatch.setattr(runner, "CallbackClient", Client)
    monkeypatch.setattr(runner, "download_input", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "extract_zip",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(runner.RunnerError("bad input")),
    )

    assert runner.run(envelope(), tmp_path) == runner.CALLBACK_FAILURE_EXIT
