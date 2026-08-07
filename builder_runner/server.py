"""Secretless, fixed-contract application build runner.

The runner deliberately depends only on the Python standard library.  It
accepts a pre-materialized build archive from the trusted coordinator, checks
and extracts it into a per-request temporary directory, runs the two fixed
toolchain commands, and returns a deterministic zip envelope.

There is no package resolution, credential handling, queue access, storage
access, or user-selected command surface in this process.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.parse import parse_qs, urlsplit

TOOLCHAIN_VERSION = "node20-vite5-v1"

PORT = int(os.environ.get("PORT", "8300"))
DEFAULT_TIMEOUT_SECONDS = 600
MAX_TIMEOUT_SECONDS = 600
REQUEST_READ_TIMEOUT_SECONDS = 30

MAX_REQUEST_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 5 * 1024 * 1024
MAX_INPUT_MEMBERS = 5_000
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_FILES = 5_000
MAX_LOG_BYTES = 1024 * 1024
MAX_ERROR_BYTES = 4 * 1024
MAX_PATH_BYTES = 1024
MAX_PATH_COMPONENT_BYTES = 255

_COPY_CHUNK_BYTES = 64 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

_INSTALL_COMMAND = (
    "npm",
    "install",
    "--offline",
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
)

# This is the only Vite config the runner executes.  The input archive also
# contains the coordinator's copy for self-description, but it is replaced
# before the build so a caller cannot smuggle executable config into Node.
_FIXED_VITE_CONFIG_NAME = ".bifrost-vite.config.mjs"
_RESERVED_INPUT_NAMES = {
    ".npmrc",
    "npm-shrinkwrap.json",
    "package-lock.json",
}
_FIXED_VITE_CONFIG = """\
import { join } from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": join(process.cwd(), "src") } },
  build: { sourcemap: false },
});
"""


class RunnerError(Exception):
    """A bounded, user-reportable build or input failure."""

    def __init__(self, error: str, log_excerpt: str = "") -> None:
        self.error = error
        self.log_excerpt = log_excerpt
        super().__init__(error)


class RequestTooLarge(RunnerError):
    """The compressed request exceeded the protocol limit."""


class _TailBuffer:
    """Thread-safe byte tail used while draining unbounded child output."""

    def __init__(self, limit: int = MAX_LOG_BYTES) -> None:
        self._limit = limit
        self._data = bytearray()
        self._lock = threading.Lock()

    def append(self, data: bytes) -> None:
        if not data or self._limit <= 0:
            return
        with self._lock:
            if len(data) >= self._limit:
                self._data = bytearray(data[-self._limit :])
                return
            overflow = len(self._data) + len(data) - self._limit
            if overflow > 0:
                del self._data[:overflow]
            self._data.extend(data)

    def text(self) -> str:
        with self._lock:
            text = bytes(self._data).decode("utf-8", errors="replace")
        while len(text.encode("utf-8")) > self._limit:
            text = text[1:]
        return text


def cap_log_tail(data: bytes | str, limit: int = MAX_LOG_BYTES) -> str:
    """Return at most ``limit`` trailing source bytes as printable text."""

    raw = data.encode("utf-8") if isinstance(data, str) else data
    tail = _TailBuffer(limit)
    tail.append(raw)
    return tail.text()


def cap_error(data: str, limit: int = MAX_ERROR_BYTES) -> str:
    """Return a valid UTF-8 error prefix within the protocol's small cap."""

    return data.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _member_path(name: str) -> tuple[PurePosixPath, bool]:
    """Return a canonical member path and whether the member names a directory."""

    if not name or "\x00" in name or "\\" in name:
        raise RunnerError(f"unsafe archive member: {name!r}")
    is_dir = name.endswith("/")
    stripped = name[:-1] if is_dir else name
    pure = PurePosixPath(stripped)
    if (
        not stripped
        or pure.is_absolute()
        or ".." in pure.parts
        or "." in pure.parts
        or str(pure) != stripped
    ):
        raise RunnerError(f"unsafe archive member: {name!r}")
    if len(stripped.encode("utf-8")) > MAX_PATH_BYTES or any(
        len(part.encode("utf-8")) > MAX_PATH_COMPONENT_BYTES for part in pure.parts
    ):
        raise RunnerError(f"archive member path is too long: {name!r}")
    return pure, is_dir


def _validate_input_member_path(pure: PurePosixPath, raw_name: str) -> None:
    folded_parts = {part.casefold() for part in pure.parts}
    if (
        "node_modules" in folded_parts
        or pure.name.casefold() in _RESERVED_INPUT_NAMES
        or str(pure) == _FIXED_VITE_CONFIG_NAME
    ):
        raise RunnerError(f"reserved build input path: {raw_name!r}")


def _validate_member_type(info: zipfile.ZipInfo, is_dir: bool) -> None:
    """Reject links, devices, and other non-file archive members."""

    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode):
        raise RunnerError(f"symlink archive member is not allowed: {info.filename}")
    if kind and not stat.S_ISREG(mode) and not (is_dir and stat.S_ISDIR(mode)):
        raise RunnerError(f"special archive member is not allowed: {info.filename}")
    if info.flag_bits & 0x1:
        raise RunnerError(f"encrypted archive member is not allowed: {info.filename}")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise RunnerError(f"unsupported zip compression for: {info.filename}")


def _validated_members(
    archive: zipfile.ZipFile,
) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    infos = archive.infolist()
    if len(infos) > MAX_INPUT_MEMBERS:
        raise RunnerError("archive exceeds file count limit")

    seen: dict[str, tuple[str, bool]] = {}
    declared_total = 0
    validated: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []

    for info in infos:
        pure, is_dir = _member_path(info.filename)
        _validate_input_member_path(pure, info.filename)
        _validate_member_type(info, is_dir)
        key = str(pure).casefold()
        if key in seen:
            raise RunnerError(
                f"duplicate archive member: {info.filename!r} conflicts with {seen[key][0]!r}"
            )

        for index in range(1, len(pure.parts)):
            ancestor = PurePosixPath(*pure.parts[:index])
            prior = seen.get(str(ancestor).casefold())
            if prior is not None and not prior[1]:
                raise RunnerError(
                    f"conflicting archive member: file {prior[0]!r} is a parent of "
                    f"{info.filename!r}"
                )
        if not is_dir:
            prefix = f"{key}/"
            descendant = next(
                (value[0] for path, value in seen.items() if path.startswith(prefix)),
                None,
            )
            if descendant is not None:
                raise RunnerError(
                    f"conflicting archive member: file {info.filename!r} is a parent of "
                    f"{descendant!r}"
                )
            if info.file_size > MAX_SOURCE_FILE_BYTES:
                raise RunnerError(
                    f"archive member exceeds file size limit: {info.filename}"
                )
            declared_total += info.file_size
            if declared_total > MAX_EXPANDED_BYTES:
                raise RunnerError("archive exceeds expanded size limit")
            validated.append((info, pure))

        seen[key] = (info.filename, is_dir)
    return validated


def _check_job_control(deadline: float, log_excerpt: str = "") -> None:
    if STATE.cancelled():
        raise RunnerError("build cancelled", log_excerpt)
    if time.monotonic() >= deadline:
        raise RunnerError("build timed out", log_excerpt)


def safe_extract_zip(
    zip_path: Path,
    destination: Path,
    *,
    deadline: float | None = None,
) -> list[str]:
    """Validate then stream-extract a build archive beneath ``destination``."""

    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = _validated_members(archive)
            extracted: list[str] = []
            expanded_total = 0
            for info, pure in members:
                target = destination.joinpath(*pure.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                try:
                    source = archive.open(info)
                except (RuntimeError, zipfile.BadZipFile) as exc:
                    raise RunnerError(
                        f"cannot read archive member: {info.filename}"
                    ) from exc
                try:
                    with source, target.open("xb") as output:
                        while True:
                            if deadline is not None:
                                _check_job_control(deadline)
                            try:
                                chunk = source.read(_COPY_CHUNK_BYTES)
                            except (RuntimeError, zipfile.BadZipFile) as exc:
                                raise RunnerError(
                                    f"corrupt archive member: {info.filename}"
                                ) from exc
                            if not chunk:
                                break
                            written += len(chunk)
                            expanded_total += len(chunk)
                            if written > MAX_SOURCE_FILE_BYTES:
                                raise RunnerError(
                                    f"archive member exceeds file size limit: {info.filename}"
                                )
                            if expanded_total > MAX_EXPANDED_BYTES:
                                raise RunnerError("archive exceeds expanded size limit")
                            output.write(chunk)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
                extracted.append(str(pure))
            return sorted(extracted)
    except zipfile.BadZipFile as exc:
        raise RunnerError("invalid zip archive") from exc


def _required_uuid(meta: dict[str, object], field: str) -> str:
    value = meta.get(field)
    if not isinstance(value, str):
        raise RunnerError(f"build-meta.json {field} must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise RunnerError(f"build-meta.json {field} must be a UUID") from exc


def _load_base(workspace: Path) -> str:
    meta_path = workspace / "build-meta.json"
    try:
        raw = meta_path.read_bytes()
    except OSError as exc:
        raise RunnerError("build-meta.json is required") from exc
    if len(raw) > MAX_SOURCE_FILE_BYTES:
        raise RunnerError("build-meta.json exceeds file size limit")
    try:
        meta = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("build-meta.json must contain valid JSON") from exc
    if not isinstance(meta, dict):
        raise RunnerError("build-meta.json must contain an object")
    app_id = _required_uuid(meta, "app_id")
    base = meta.get("base")
    if "solution_id" in meta:
        solution_id = _required_uuid(meta, "solution_id")
        expected = f"/{solution_id}/apps/{app_id}/"
    else:
        expected = f"/api/applications/{app_id}/dist/"
    if base != expected:
        raise RunnerError("build-meta.json base does not match its scope")
    return expected


def _vite_command(base: str) -> tuple[str, ...]:
    return (
        "npx",
        "vite",
        "build",
        "--config",
        _FIXED_VITE_CONFIG_NAME,
        "--base",
        base,
    )


def _child_environment(
    npm_cache: Path | str = "/opt/npm-cache",
    *,
    home: Path | str = "/tmp",
    temp_dir: Path | str = "/tmp",
) -> dict[str, str]:
    """Return the credential-free environment visible to generated builds."""

    return {
        "CI": "true",
        "HOME": str(home),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "TMPDIR": str(temp_dir),
        "XDG_CACHE_HOME": str(Path(home) / ".cache"),
        "npm_config_audit": "false",
        "npm_config_cache": str(npm_cache),
        "npm_config_fund": "false",
        "npm_config_ignore_scripts": "true",
        "npm_config_offline": "true",
        "npm_config_update_notifier": "false",
    }


class RunnerState:
    """One-build gate plus the current process-group cancellation handle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False
        self._cancelled = False
        self._process: subprocess.Popen[bytes] | None = None

    def begin(self) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._cancelled = False
            self._process = None
            return True

    def finish(self) -> None:
        with self._lock:
            self._process = None
            self._cancelled = False
            self._busy = False

    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def register_process(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._process = process
            cancel_now = self._cancelled
        if cancel_now:
            _kill_process_group(process)

    def clear_process(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            if self._process is process:
                self._process = None

    def cancel(self) -> None:
        with self._lock:
            if not self._busy:
                return
            self._cancelled = True
            process = self._process
        if process is not None:
            _kill_process_group(process)


STATE = RunnerState()


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _drain_output(stream: BinaryIO, tail: _TailBuffer) -> None:
    try:
        while chunk := stream.read(_COPY_CHUNK_BYTES):
            tail.append(chunk)
    finally:
        stream.close()


def _run_command(
    command: tuple[str, ...],
    *,
    workspace: Path,
    deadline: float,
    tail: _TailBuffer,
    environment: dict[str, str],
) -> None:
    if STATE.cancelled():
        raise RunnerError("build cancelled", tail.text())
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RunnerError("build timed out", tail.text())

    try:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        raise RunnerError("build toolchain unavailable", tail.text()) from exc

    STATE.register_process(process)
    assert process.stdout is not None
    reader = threading.Thread(
        target=_drain_output, args=(process.stdout, tail), daemon=True
    )
    reader.start()
    timed_out = False
    try:
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(process)
            return_code = process.wait()
    finally:
        reader.join(timeout=5)
        STATE.clear_process(process)

    if timed_out:
        raise RunnerError("build timed out", tail.text())
    if STATE.cancelled():
        raise RunnerError("build cancelled", tail.text())
    if return_code != 0:
        executable = command[0]
        raise RunnerError(f"{executable} exited with status {return_code}", tail.text())


def _output_files(
    dist_dir: Path,
    *,
    deadline: float | None = None,
) -> list[tuple[str, Path]]:
    if not dist_dir.is_dir() or dist_dir.is_symlink():
        raise RunnerError("build did not produce a dist directory")

    files: list[tuple[str, Path]] = []
    total = 0
    for current_root, directories, names in os.walk(dist_dir, followlinks=False):
        if deadline is not None:
            _check_job_control(deadline)
        root = Path(current_root)
        for directory in directories:
            path = root / directory
            if path.is_symlink():
                raise RunnerError(f"build output contains a symlink: {path.name}")
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise RunnerError(
                    f"build output contains a special directory: {path.name}"
                )
        for name in names:
            path = root / name
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise RunnerError(f"build output contains a special file: {path.name}")
            relative = path.relative_to(dist_dir).as_posix()
            _member_path(relative)
            size = path.stat().st_size
            total += size
            if total > MAX_OUTPUT_BYTES:
                raise RunnerError("build output exceeds size limit")
            files.append((relative, path))
            if len(files) > MAX_OUTPUT_FILES:
                raise RunnerError("build output exceeds file count limit")
    return sorted(files)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.flag_bits |= 0x800
    return info


def write_response_zip(
    dist_dir: Path,
    destination: Path,
    *,
    duration_ms: int,
    log_excerpt: str,
    deadline: float | None = None,
) -> None:
    """Write sorted ``dist/**`` plus canonical ``build.json`` zip metadata."""

    files = _output_files(dist_dir, deadline=deadline)
    build_json = json.dumps(
        {
            "duration_ms": duration_ms,
            "log_excerpt": cap_log_tail(log_excerpt),
            "ok": True,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative, path in files:
            with archive.open(_zip_info(f"dist/{relative}"), "w") as output:
                with path.open("rb") as source:
                    while chunk := source.read(_COPY_CHUNK_BYTES):
                        if deadline is not None:
                            _check_job_control(deadline, log_excerpt)
                        output.write(chunk)
        archive.writestr(_zip_info("build.json"), build_json)


def execute_build(input_zip: Path, response_zip: Path, timeout_s: int) -> None:
    """Execute one validated build, always deleting its extracted workspace."""

    started = time.monotonic()
    deadline = started + timeout_s
    scratch = Path(tempfile.mkdtemp(prefix="bifrost-runner-"))
    workspace = scratch / "workspace"
    workspace.mkdir()
    tail = _TailBuffer()
    try:
        safe_extract_zip(input_zip, workspace, deadline=deadline)
        base = _load_base(workspace)
        fixed_config = workspace / _FIXED_VITE_CONFIG_NAME
        if fixed_config.exists() and not fixed_config.is_file():
            raise RunnerError(
                f"reserved build path is not a file: {_FIXED_VITE_CONFIG_NAME}"
            )
        fixed_config.write_text(_FIXED_VITE_CONFIG, encoding="utf-8")
        runtime_cache = scratch / "npm-cache"
        try:
            shutil.copytree("/opt/npm-cache", runtime_cache)
        except OSError as exc:
            raise RunnerError("build toolchain cache unavailable") from exc
        _check_job_control(deadline, tail.text())
        runtime_home = scratch / "home"
        runtime_temp = scratch / "tmp"
        runtime_home.mkdir()
        runtime_temp.mkdir()
        environment = _child_environment(
            runtime_cache,
            home=runtime_home,
            temp_dir=runtime_temp,
        )
        _run_command(
            _INSTALL_COMMAND,
            workspace=workspace,
            deadline=deadline,
            tail=tail,
            environment=environment,
        )
        _run_command(
            _vite_command(base),
            workspace=workspace,
            deadline=deadline,
            tail=tail,
            environment=environment,
        )
        if STATE.cancelled():
            raise RunnerError("build cancelled", tail.text())
        duration_ms = int((time.monotonic() - started) * 1000)
        write_response_zip(
            workspace / "dist",
            response_zip,
            duration_ms=duration_ms,
            log_excerpt=tail.text(),
            deadline=deadline,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _parse_timeout(raw_path: str) -> int:
    query = parse_qs(urlsplit(raw_path).query, keep_blank_values=True)
    values = query.get("timeout_s", [str(DEFAULT_TIMEOUT_SECONDS)])
    if len(values) != 1:
        raise RunnerError("timeout_s must be specified once")
    try:
        timeout = int(values[0])
    except ValueError as exc:
        raise RunnerError("timeout_s must be an integer") from exc
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise RunnerError(f"timeout_s must be between 1 and {MAX_TIMEOUT_SECONDS}")
    return timeout


class RunnerRequestHandler(BaseHTTPRequestHandler):
    """Narrow internal HTTP protocol consumed only by the coordinator."""

    server_version = "BifrostBuilderRunner/1"
    sys_version = ""

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_zip(self, path: Path) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile, length=_COPY_CHUNK_BYTES)

    def _read_request(self, destination: Path) -> None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise RunnerError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise RunnerError("Content-Length must be an integer") from exc
        if length < 0:
            raise RunnerError("Content-Length cannot be negative")
        if length > MAX_REQUEST_BYTES:
            raise RequestTooLarge("source archive exceeds request size limit")

        read_deadline = time.monotonic() + REQUEST_READ_TIMEOUT_SECONDS
        remaining = length
        with destination.open("xb") as output:
            while remaining:
                read_remaining = read_deadline - time.monotonic()
                if read_remaining <= 0:
                    raise RunnerError("request body timed out")
                self.connection.settimeout(read_remaining)
                try:
                    chunk = self.rfile.read(min(_COPY_CHUNK_BYTES, remaining))
                except (TimeoutError, socket.timeout) as exc:
                    raise RunnerError("request body timed out") from exc
                if not chunk:
                    raise RunnerError("request body ended before Content-Length")
                output.write(chunk)
                remaining -= len(chunk)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if urlsplit(self.path).path != "/healthz":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._send_json(
            HTTPStatus.OK,
            {"busy": STATE.busy(), "toolchain": TOOLCHAIN_VERSION},
        )

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        route = urlsplit(self.path).path
        if route == "/cancel":
            STATE.cancel()
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if route != "/build":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not STATE.begin():
            self._send_json(HTTPStatus.CONFLICT, {"error": "runner busy"})
            return

        request_dir: Path | None = None
        try:
            timeout_s = _parse_timeout(self.path)
            request_dir = Path(tempfile.mkdtemp(prefix="bifrost-request-"))
            input_zip = request_dir / "input.zip"
            response_zip = request_dir / "response.zip"
            self._read_request(input_zip)
            execute_build(input_zip, response_zip, timeout_s)
            STATE.finish()
            self._send_zip(response_zip)
        except RequestTooLarge as exc:
            STATE.finish()
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": cap_error(exc.error), "log_excerpt": exc.log_excerpt},
            )
        except RunnerError as exc:
            STATE.finish()
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {
                    "error": cap_error(exc.error),
                    "log_excerpt": cap_log_tail(exc.log_excerpt),
                },
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            STATE.finish()
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "internal runner error", "log_excerpt": ""},
            )
        finally:
            if request_dir is not None:
                shutil.rmtree(request_dir, ignore_errors=True)
            STATE.finish()

    def log_message(self, format: str, *args: object) -> None:
        # Keep only the stdlib's concise request record. Build logs are returned
        # through the bounded protocol field, never copied to server stdout.
        super().log_message(format, *args)


class RunnerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def main() -> None:
    server = RunnerHTTPServer(("0.0.0.0", PORT), RunnerRequestHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
