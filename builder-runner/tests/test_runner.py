from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

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


def test_workspace_tool_rejects_escape(tmp_path: Path) -> None:
    response = runner._execute_workspace_tool(
        tmp_path,
        {"id": "call_1", "name": "write_file", "arguments": {"path": "../x", "content": "bad"}},
        allowed_tools={"write_file"},
    )
    assert response["role"] == "tool"
    assert "unsafe archive member" in response["content"]


def test_runner_probe_and_file_envelope(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert runner.main(["--probe"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ready": True, "schema_version": 1}

    path = tmp_path / "envelope.json"
    path.write_text(json.dumps(envelope().__dict__), encoding="utf-8")
    parsed: list[runner.Envelope] = []
    monkeypatch.setenv("BIFROST_RUNNER_WORKDIR", str(tmp_path / "work"))
    monkeypatch.setattr(runner, "run", lambda value, _root: parsed.append(value) or 0)
    assert runner.main([str(path)]) == 0
    assert parsed == [envelope()]


def test_build_commands_use_fixed_bifrost_toolchain(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"private": True, "scripts": {"build": "malicious-command"}}),
        encoding="utf-8",
    )
    (tmp_path / "build-meta.json").write_text(
        json.dumps({"base": "/solution/apps/app/"}),
        encoding="utf-8",
    )
    install, build = runner.build_commands(tmp_path)
    assert install == ("npm", "install", "--ignore-scripts", "--no-audit", "--no-fund")
    assert build[:5] == ("npx", "--no-install", "vite", "build", "--config")
    assert "malicious-command" not in build


def test_builder_tools_match_native_catalog() -> None:
    enabled = [
        "list_files",
        "read_file",
        "search_text",
        "write_file",
        "apply_patch",
        "delete_file",
        "make_directory",
        "validate_solution",
    ]
    assert [tool["name"] for tool in runner._builder_tools(enabled, None)] == enabled
    assert runner._builder_tools(enabled, "skills/example")[-1]["name"] == "read_skill_asset"


def test_builder_tools_enforce_negotiated_catalog(tmp_path: Path) -> None:
    assert [
        tool["name"]
        for tool in runner._builder_tools(["read_file", "list_files"], None)
    ] == ["read_file", "list_files"]
    with pytest.raises(runner.RunnerError, match="Unsupported Builder system tools"):
        runner._builder_tools(["execute_workflow"], None)

    response = runner._execute_workspace_tool(
        tmp_path,
        {"id": "call_1", "name": "write_file", "arguments": {"path": "x", "content": "bad"}},
        allowed_tools={"read_file"},
    )
    assert "not enabled" in response["content"]
    assert not (tmp_path / "x").exists()


def test_cloudflare_sdk_and_runner_base_image_versions_match() -> None:
    root = Path(__file__).resolve().parents[2]
    package = json.loads(
        (root / "api/src/services/cloudflare_runner/package.json").read_text(
            encoding="utf-8"
        )
    )
    dockerfile = (root / "builder-runner/Dockerfile").read_text(encoding="utf-8")
    version = package["dependencies"]["@cloudflare/sandbox"]
    assert f"FROM docker.io/cloudflare/sandbox:{version}" in dockerfile
    assert "bifrost-sandbox-runner" in dockerfile


def test_search_tool_rejects_parent_glob(tmp_path: Path) -> None:
    response = runner._execute_workspace_tool(
        tmp_path,
        {
            "id": "call_1",
            "name": "search_text",
            "arguments": {"pattern": "secret", "glob": "../*"},
        },
        allowed_tools={"search_text"},
    )
    assert "unsafe archive member" in response["content"]


def test_workspace_tool_rejects_symlink_component(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "linked").symlink_to(outside, target_is_directory=True)
    response = runner._execute_workspace_tool(
        tmp_path / "workspace",
        {
            "id": "call_1",
            "name": "write_file",
            "arguments": {"path": "linked/escape.txt", "content": "bad"},
        },
        allowed_tools={"write_file"},
    )
    assert "symlink" in response["content"]
    assert not (outside / "escape.txt").exists()


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

        def complete_turn(self, body: dict[str, object]) -> None:
            self.completed = body

    turn_envelope = envelope(job_type="solution.builder.turn")
    client = Client(turn_envelope)
    monkeypatch.setattr(runner, "CallbackClient", lambda _envelope: client)
    monkeypatch.setattr(runner, "download_input", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "extract_zip", lambda *_args, **_kwargs: None)

    def cancel(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise runner.Cancelled("job cancelled")

    monkeypatch.setattr(runner, "run_turn_loop", cancel)
    code = runner.run(turn_envelope, tmp_path)
    assert code == 2
    assert client.completed == {"status": "cancelled", "error": "job cancelled"}
