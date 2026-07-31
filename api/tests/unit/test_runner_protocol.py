"""Pure protocol tests for the stdlib-only secretless builder runner."""

from __future__ import annotations

import http.client
import importlib.util
import json
import stat
import sys
import threading
import zipfile
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from src.services.builder.fs_tools import (
    WorkspaceLimits,
    WorkspaceViolation,
    safe_extract_zip as api_safe_extract_zip,
)


def _runner_path() -> Path:
    candidates = (
        Path("/builder_runner/server.py"),
        Path("/app/builder_runner/server.py"),
        Path(__file__).resolve().parents[3] / "builder_runner" / "server.py",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "builder_runner/server.py is not mounted in the test runner"
    )


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "builder_runner_server", _runner_path()
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


@pytest.fixture
def protocol_server(monkeypatch: pytest.MonkeyPatch):
    state = runner.RunnerState()
    monkeypatch.setattr(runner, "STATE", state)
    server = runner.RunnerHTTPServer(
        ("127.0.0.1", 0),
        runner.RunnerRequestHandler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _zip_with_member(
    path: Path,
    name: str,
    data: bytes = b"x",
    *,
    mode: int = stat.S_IFREG | 0o644,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, data)


@pytest.mark.parametrize(
    "member",
    [
        "/absolute.txt",
        "../outside.txt",
        "src/../../outside.txt",
        r"src\windows.txt",
        "src//alias.txt",
        "src/./alias.txt",
    ],
)
def test_safe_extract_rejects_unsafe_paths(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "input.zip"
    _zip_with_member(archive, member)

    with pytest.raises(runner.RunnerError, match="unsafe archive member"):
        runner.safe_extract_zip(archive, tmp_path / "output")
    if member not in {"src//alias.txt", "src/./alias.txt"}:
        with pytest.raises(WorkspaceViolation):
            api_safe_extract_zip(
                archive,
                tmp_path / "api-output",
                WorkspaceLimits(),
            )


@pytest.mark.parametrize(
    "mode,reason",
    [
        (stat.S_IFLNK | 0o777, "symlink"),
        (stat.S_IFCHR | 0o600, "special"),
        (stat.S_IFBLK | 0o600, "special"),
        (stat.S_IFIFO | 0o600, "special"),
    ],
)
def test_safe_extract_rejects_links_and_devices(
    tmp_path: Path, mode: int, reason: str
) -> None:
    archive = tmp_path / "input.zip"
    _zip_with_member(archive, "src/item", mode=mode)

    with pytest.raises(runner.RunnerError, match=reason):
        runner.safe_extract_zip(archive, tmp_path / "output")
    if reason == "symlink":
        with pytest.raises(WorkspaceViolation, match="symlink"):
            api_safe_extract_zip(
                archive,
                tmp_path / "api-output",
                WorkspaceLimits(),
            )


def test_safe_extract_rejects_casefolded_duplicates_before_writing(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("src/App.tsx", b"first")
        zipped.writestr("src/app.tsx", b"second")

    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(runner.RunnerError, match="duplicate archive member"):
        runner.safe_extract_zip(archive, output)
    assert list(output.rglob("*")) == []


def test_safe_extract_rejects_file_directory_conflicts(tmp_path: Path) -> None:
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("src", b"file")
        zipped.writestr("src/main.tsx", b"child")

    with pytest.raises(runner.RunnerError, match="conflicting archive member"):
        runner.safe_extract_zip(archive, tmp_path / "output")


@pytest.mark.parametrize(
    "member",
    [
        "package-lock.json",
        "src/.npmrc",
        "node_modules/.bin/vite",
        ".bifrost-vite.config.mjs",
    ],
)
def test_safe_extract_rejects_toolchain_control_paths(
    tmp_path: Path, member: str
) -> None:
    archive = tmp_path / "input.zip"
    _zip_with_member(archive, member)

    with pytest.raises(runner.RunnerError, match="reserved build input path"):
        runner.safe_extract_zip(archive, tmp_path / "output")


def test_safe_extract_enforces_declared_and_streamed_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "input.zip"
    _zip_with_member(archive, "large.txt", b"12345")
    monkeypatch.setattr(runner, "MAX_SOURCE_FILE_BYTES", 4)

    with pytest.raises(runner.RunnerError, match="file size limit"):
        runner.safe_extract_zip(archive, tmp_path / "output")


def test_safe_extract_enforces_member_count_and_expanded_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("one.txt", b"123")
        zipped.writestr("two.txt", b"45")

    monkeypatch.setattr(runner, "MAX_INPUT_MEMBERS", 1)
    with pytest.raises(runner.RunnerError, match="file count"):
        runner.safe_extract_zip(archive, tmp_path / "count-output")

    monkeypatch.setattr(runner, "MAX_INPUT_MEMBERS", 2)
    monkeypatch.setattr(runner, "MAX_EXPANDED_BYTES", 4)
    with pytest.raises(runner.RunnerError, match="expanded size"):
        runner.safe_extract_zip(archive, tmp_path / "size-output")


def test_safe_extract_valid_archive(tmp_path: Path) -> None:
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("src/main.tsx", b"export default 1;\n")
        zipped.writestr("package.json", b"{}")

    output = tmp_path / "output"
    output.mkdir()
    assert runner.safe_extract_zip(archive, output) == [
        "package.json",
        "src/main.tsx",
    ]
    assert (output / "src" / "main.tsx").read_bytes() == b"export default 1;\n"


def test_log_tail_is_byte_capped_and_keeps_end() -> None:
    log = b"discard-me\n" + b"x" * 20 + b"\nimportant ending"
    result = runner.cap_log_tail(log, 20)

    assert "discard-me" not in result
    assert result.endswith("important ending")
    assert len(result.encode()) <= 20


def test_log_tail_stays_byte_capped_across_partial_utf8() -> None:
    result = runner.cap_log_tail(("🙂" * 10).encode(), 5)

    assert len(result.encode()) <= 5
    assert result.endswith("🙂")


def test_error_prefix_is_byte_capped() -> None:
    result = runner.cap_error("🙂" * 2_000, 17)

    assert len(result.encode()) <= 17
    assert result == "🙂" * 4


def test_response_zip_is_deterministic_and_contains_build_contract(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<main>ok</main>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export {}", encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    kwargs = {"duration_ms": 123, "log_excerpt": "built\n"}
    runner.write_response_zip(dist, first, **kwargs)
    runner.write_response_zip(dist, second, **kwargs)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == [
            "dist/assets/app.js",
            "dist/index.html",
            "build.json",
        ]
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
        )
        assert json.loads(archive.read("build.json")) == {
            "duration_ms": 123,
            "log_excerpt": "built\n",
            "ok": True,
        }


def test_response_zip_rejects_output_symlink(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret", encoding="utf-8")
    (dist / "leak").symlink_to(outside)

    with pytest.raises(runner.RunnerError, match="special file"):
        runner.write_response_zip(
            dist,
            tmp_path / "response.zip",
            duration_ms=1,
            log_excerpt="",
        )


def test_response_zip_enforces_output_size_and_file_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "one.txt").write_bytes(b"12")

    monkeypatch.setattr(runner, "MAX_OUTPUT_BYTES", 1)
    with pytest.raises(runner.RunnerError, match="output exceeds size"):
        runner.write_response_zip(
            dist,
            tmp_path / "size.zip",
            duration_ms=1,
            log_excerpt="",
        )

    monkeypatch.setattr(runner, "MAX_OUTPUT_BYTES", 100)
    monkeypatch.setattr(runner, "MAX_OUTPUT_FILES", 0)
    with pytest.raises(runner.RunnerError, match="file count"):
        runner.write_response_zip(
            dist,
            tmp_path / "count.zip",
            duration_ms=1,
            log_excerpt="",
        )


def test_build_meta_requires_exact_app_scoped_base(tmp_path: Path) -> None:
    app_id = "fd400706-10c7-438f-8793-1e9a576dd90d"
    meta = tmp_path / "build-meta.json"
    meta.write_text(
        json.dumps(
            {
                "app_id": app_id,
                "base": f"/api/applications/{app_id}/dist/",
            }
        ),
        encoding="utf-8",
    )
    assert runner._load_base(tmp_path) == f"/api/applications/{app_id}/dist/"

    meta.write_text(
        json.dumps({"app_id": app_id, "base": "https://attacker.invalid/"}),
        encoding="utf-8",
    )
    with pytest.raises(runner.RunnerError, match="base does not match its scope"):
        runner._load_base(tmp_path)


def test_build_meta_accepts_exact_solution_scoped_base(tmp_path: Path) -> None:
    app_id = "fd400706-10c7-438f-8793-1e9a576dd90d"
    solution_id = "7c209806-cce2-4215-b07d-14d7bb45d6d1"
    expected = f"/{solution_id}/apps/{app_id}/"
    meta = tmp_path / "build-meta.json"
    meta.write_text(
        json.dumps(
            {
                "app_id": app_id,
                "solution_id": solution_id,
                "base": expected,
            }
        ),
        encoding="utf-8",
    )
    assert runner._load_base(tmp_path) == expected

    meta.write_text(
        json.dumps(
            {
                "app_id": app_id,
                "solution_id": solution_id,
                "base": f"/api/applications/{app_id}/dist/",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(runner.RunnerError, match="base does not match its scope"):
        runner._load_base(tmp_path)


def test_build_meta_rejects_invalid_optional_solution_id(tmp_path: Path) -> None:
    app_id = "fd400706-10c7-438f-8793-1e9a576dd90d"
    (tmp_path / "build-meta.json").write_text(
        json.dumps(
            {
                "app_id": app_id,
                "solution_id": None,
                "base": f"/api/applications/{app_id}/dist/",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(runner.RunnerError, match="solution_id must be a UUID"):
        runner._load_base(tmp_path)


def test_health_and_cancel_http_contract(protocol_server) -> None:
    port, state = protocol_server
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/healthz")
    response = connection.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type") == "application/json"
    assert json.loads(response.read()) == {
        "busy": False,
        "toolchain": "node20-vite5-v1",
    }

    assert state.begin()
    connection.request("POST", "/cancel", body=b"")
    response = connection.getresponse()
    assert response.status == 204
    assert response.read() == b""
    assert state.cancelled()
    state.finish()
    connection.close()


def test_build_busy_http_contract(protocol_server) -> None:
    port, state = protocol_server
    assert state.begin()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        "POST",
        "/build?timeout_s=1",
        body=b"",
        headers={"Content-Type": "application/zip"},
    )
    response = connection.getresponse()
    assert response.status == 409
    assert json.loads(response.read()) == {"error": "runner busy"}
    state.finish()
    connection.close()


def test_build_validation_failure_releases_gate(protocol_server) -> None:
    port, state = protocol_server
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        "POST",
        "/build?timeout_s=0",
        body=b"",
        headers={"Content-Type": "application/zip"},
    )
    response = connection.getresponse()
    assert response.status == 422
    assert json.loads(response.read()) == {
        "error": "timeout_s must be between 1 and 600",
        "log_excerpt": "",
    }
    assert not state.busy()
    connection.close()


def test_oversized_request_http_contract(
    protocol_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    port, state = protocol_server
    monkeypatch.setattr(runner, "MAX_REQUEST_BYTES", 1)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        "POST",
        "/build?timeout_s=1",
        body=b"12",
        headers={"Content-Type": "application/zip"},
    )
    response = connection.getresponse()
    assert response.status == 413
    assert json.loads(response.read()) == {
        "error": "source archive exceeds request size limit",
        "log_excerpt": "",
    }
    assert not state.busy()
    connection.close()


def test_commands_are_fixed_and_offline() -> None:
    assert runner._INSTALL_COMMAND == (
        "npm",
        "install",
        "--offline",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    )
    command = runner._vite_command("/api/applications/id/dist/")
    assert command[:3] == ("npx", "vite", "build")
    assert command[3:5] == ("--config", ".bifrost-vite.config.mjs")
    assert runner._child_environment()["npm_config_offline"] == "true"
    environment = runner._child_environment(
        "/tmp/job-cache",
        home="/tmp/job-home",
        temp_dir="/tmp/job-tmp",
    )
    assert environment["npm_config_cache"] == "/tmp/job-cache"
    assert environment["HOME"] == "/tmp/job-home"
    assert environment["TMPDIR"] == "/tmp/job-tmp"
    assert environment["XDG_CACHE_HOME"] == "/tmp/job-home/.cache"
    assert "PORT" not in environment


def test_runner_state_allows_only_one_build() -> None:
    state = runner.RunnerState()
    assert state.begin() is True
    assert state.begin() is False
    assert state.busy() is True
    state.finish()
    assert state.busy() is False
    assert state.begin() is True


def test_runner_state_cancel_kills_registered_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = runner.RunnerState()
    process = Mock()
    killed: list[object] = []
    monkeypatch.setattr(runner, "_kill_process_group", killed.append)

    assert state.begin()
    state.register_process(process)
    state.cancel()

    assert killed == [process]
    assert state.cancelled()


def test_execute_build_cleans_workspace_on_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def reject_input(*_args, **_kwargs) -> None:
        raise runner.RunnerError("bad input")

    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(scratch))
    monkeypatch.setattr(runner, "safe_extract_zip", reject_input)

    with pytest.raises(runner.RunnerError, match="bad input"):
        runner.execute_build(
            tmp_path / "input.zip",
            tmp_path / "output.zip",
            timeout_s=1,
        )
    assert not scratch.exists()
