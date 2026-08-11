#!/usr/bin/env python3
"""Provider-neutral Bifrost builder sandbox harness."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

SCHEMA_VERSION = 1
MAX_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_EXPANDED_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 10_000
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
MAX_LOG_BYTES = 128 * 1024
MAX_TOOL_FILE_BYTES = 5 * 1024 * 1024
MAX_TOOL_WORKSPACE_BYTES = 200 * 1024 * 1024
MAX_TOOL_WORKSPACE_FILES = 5_000
MAX_ENVELOPE_BYTES = 1024 * 1024
MAX_HARNESS_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_HARNESS_STATE_BYTES = 50 * 1024 * 1024
REPORTED_FAILURE_EXIT = 1
REPORTED_CANCELLED_EXIT = 2
CALLBACK_FAILURE_EXIT = 3
CALLBACK_RETRY_ATTEMPTS = 3
CALLBACK_RETRY_BASE_SECONDS = 1
CANCELLATION_CHECK_TIMEOUT_SECONDS = 5
OPENCODE_PROVIDER_TIMEOUT_MS = 15 * 60 * 1000
OPENCODE_CONTEXT_WINDOW = 64_000
OPENCODE_OUTPUT_LIMIT = 16_384
OPENCODE_HELPER = Path(__file__).resolve().with_name("opencode_turn.mjs")
COPY_CHUNK_BYTES = 1024 * 1024
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
JOB_TYPES = {"solution.build", "solution.builder.turn"}
RUNNER_USER_AGENT = "Bifrost-Builder-Runner/1.0"
NATIVE_BUILDER_RUNTIME_CONTRACT = """## Native Builder Runtime Contract

You are running inside Bifrost's native private Solution Builder. This contract
specializes the general Bifrost Build Skill for this environment:

- The current directory is already the authenticated v2 Solution workspace.
- The Solution owner, organization, and access policy are already captured by Bifrost.
  Do not run CLI authentication/environment probes and do not ask for those choices.
- Do not mutate live platform entities and do not deploy. Author files only inside this
  workspace; Bifrost validates, versions, builds, and deploys the returned revision.
- Read the relevant bundled references before editing, inspect the existing workspace,
  keep a concise task list, and implement the user's complete request autonomously.
- Do not edit the bundled `skills/bifrost-build` Skill or the private Builder Agent
  definition unless the user explicitly asks to change Builder itself.
- Never start a development server, watcher, interactive process, or other command that
  waits indefinitely. Use bounded, one-shot build, typecheck, lint, and test commands.
- Validate the Solution with the tools available in this sandbox. Once the requested
  behavior is implemented and validation passes, stop and give a concise user-facing
  summary. Do not repeat completed discovery or continue polishing without a concrete
  unmet requirement.
"""


class RunnerError(Exception):
    def __init__(
        self,
        message: str,
        *,
        log_excerpt: str = "",
        harness_session_id: str | None = None,
        harness_diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.log_excerpt = log_excerpt
        self.harness_session_id = harness_session_id
        self.harness_diagnostics = harness_diagnostics


class Cancelled(RunnerError):
    pass


class CallbackRequestError(RunnerError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclasses.dataclass(frozen=True)
class Envelope:
    schema_version: int
    job_id: str
    job_type: str
    dispatch_attempt: int
    callback_base_url: str
    capability: str
    input_sha256: str
    timeout_seconds: int

    @classmethod
    def parse(cls, raw: bytes) -> "Envelope":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RunnerError("envelope must be valid JSON") from exc
        if not isinstance(data, dict):
            raise RunnerError("envelope must be a JSON object")
        allowed = {field.name for field in dataclasses.fields(cls)}
        extra = sorted(set(data) - allowed)
        if extra:
            raise RunnerError(f"unknown envelope fields: {', '.join(extra)}")
        missing = sorted(allowed - set(data))
        if missing:
            raise RunnerError(f"missing envelope fields: {', '.join(missing)}")
        envelope = cls(**data)
        envelope.validate()
        return envelope

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise RunnerError("unsupported envelope schema_version")
        try:
            uuid.UUID(self.job_id)
        except ValueError as exc:
            raise RunnerError("job_id must be a UUID") from exc
        if self.job_type not in JOB_TYPES:
            raise RunnerError("job_type is not supported")
        if not isinstance(self.dispatch_attempt, int) or self.dispatch_attempt < 1:
            raise RunnerError("dispatch_attempt must be a positive integer")
        parsed = urlsplit(self.callback_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RunnerError("callback_base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise RunnerError("callback_base_url cannot contain credentials or a fragment")
        if not isinstance(self.capability, str) or not self.capability.strip():
            raise RunnerError("capability is required")
        if (
            not isinstance(self.input_sha256, str)
            or len(self.input_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.input_sha256)
        ):
            raise RunnerError("input_sha256 must be a lowercase SHA-256 hex digest")
        if not isinstance(self.timeout_seconds, int) or self.timeout_seconds < 1:
            raise RunnerError("timeout_seconds must be a positive integer")
        if self.timeout_seconds > MAX_TIMEOUT_SECONDS:
            raise RunnerError("timeout_seconds exceeds runner limit")


class CallbackClient:
    def __init__(self, envelope: Envelope) -> None:
        self.envelope = envelope
        self.base = (
            envelope.callback_base_url.rstrip("/")
            + f"/api/internal/sandbox/jobs/{envelope.job_id}"
        )
        self.headers = {
            "Authorization": f"Bearer {envelope.capability}",
            "User-Agent": RUNNER_USER_AGENT,
        }
        self._cancel_check_failures = 0

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 30,
        attempts: int = CALLBACK_RETRY_ATTEMPTS,
    ) -> bytes:
        headers = dict(self.headers)
        payload = body
        if json_body is not None:
            payload = json.dumps(json_body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        req = Request(self.base + path, data=payload, headers=headers, method=method)
        for attempt in range(max(1, attempts)):
            try:
                with urlopen(req, timeout=timeout) as response:
                    return response.read()
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:4000]
                error = CallbackRequestError(
                    f"callback {method} {path} failed: {exc.code} {detail}",
                    retryable=exc.code in {408, 429} or exc.code >= 500,
                )
            except (URLError, TimeoutError) as exc:
                reason = getattr(exc, "reason", str(exc))
                error = CallbackRequestError(
                    f"callback {method} {path} failed: {reason}",
                    retryable=True,
                )
            if not error.retryable or attempt + 1 >= max(1, attempts):
                raise error
            time.sleep(CALLBACK_RETRY_BASE_SECONDS * (2**attempt))
        raise AssertionError("callback retry loop did not return or raise")

    def get_json(
        self,
        path: str,
        *,
        timeout: float = 30,
        attempts: int = CALLBACK_RETRY_ATTEMPTS,
    ) -> dict[str, Any]:
        body = self.request("GET", path, timeout=timeout, attempts=attempts)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RunnerError(f"callback {path} did not return JSON") from exc
        if not isinstance(data, dict):
            raise RunnerError(f"callback {path} returned non-object JSON")
        return data

    def progress(self, phase: str, current: int = 0, total: int | None = None) -> None:
        payload: dict[str, Any] = {"phase": phase[:200], "current": max(0, current)}
        if total is not None:
            payload["total"] = max(0, total)
            payload["percent"] = 100.0 if total == 0 else min(100.0, current / total * 100)
        try:
            self.request("POST", "/progress", json_body=payload, timeout=10)
        except CallbackRequestError as exc:
            if not exc.retryable:
                raise
            print(f"Transient progress callback failure: {exc.message}", file=sys.stderr)

    def ensure_not_cancelled(self) -> None:
        try:
            data = self.get_json(
                "/cancelled",
                timeout=CANCELLATION_CHECK_TIMEOUT_SECONDS,
                attempts=1,
            )
        except CallbackRequestError as exc:
            if not exc.retryable:
                raise
            self._cancel_check_failures += 1
            if self._cancel_check_failures == 1 or self._cancel_check_failures % 60 == 0:
                print(
                    f"Transient cancellation callback failure: {exc.message}",
                    file=sys.stderr,
                )
            return
        self._cancel_check_failures = 0
        if data.get("cancelled") is True:
            raise Cancelled("job cancelled")

    def complete_build(
        self,
        status: str,
        *,
        manifest: list[dict[str, Any]] | None = None,
        error: str | None = None,
        log_excerpt: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"status": status}
        if manifest is not None:
            body["output_manifest"] = manifest
        if error:
            body["error"] = error[:4000]
        if log_excerpt:
            body["log_excerpt"] = log_excerpt[-MAX_LOG_BYTES:]
        self.request("POST", "/complete", json_body=body)

    def complete_turn(self, body: dict[str, Any]) -> None:
        self.request("POST", "/complete", json_body=body)


def _safe_member(name: str) -> tuple[PurePosixPath, bool]:
    if not name or "\x00" in name or "\\" in name:
        raise RunnerError(f"unsafe archive member: {name!r}")
    is_dir = name.endswith("/")
    stripped = name[:-1] if is_dir else name
    pure = PurePosixPath(stripped)
    if not stripped or pure.is_absolute() or ".." in pure.parts or str(pure) != stripped:
        raise RunnerError(f"unsafe archive member: {name!r}")
    if any(part in {"", "."} for part in pure.parts):
        raise RunnerError(f"unsafe archive member: {name!r}")
    return pure, is_dir


def _validate_zip_info(info: zipfile.ZipInfo, is_dir: bool) -> None:
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode):
        raise RunnerError(f"symlink archive member is not allowed: {info.filename}")
    if kind and not stat.S_ISREG(mode) and not (is_dir and stat.S_ISDIR(mode)):
        raise RunnerError(f"special archive member is not allowed: {info.filename}")
    if info.flag_bits & 0x1:
        raise RunnerError(f"encrypted archive member is not allowed: {info.filename}")


def download_input(client: CallbackClient, destination: Path, expected_sha: str) -> None:
    digest = hashlib.sha256()
    total = 0
    req = Request(client.base + "/input", headers=client.headers, method="GET")
    try:
        with urlopen(req, timeout=60) as response, destination.open("wb") as output:
            while True:
                chunk = response.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_INPUT_BYTES:
                    raise RunnerError("input archive exceeds size limit")
                digest.update(chunk)
                output.write(chunk)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:4000]
        raise RunnerError(f"callback GET /input failed: {exc.code} {detail}") from exc
    except URLError as exc:
        raise RunnerError(f"callback GET /input failed: {exc.reason}") from exc
    if digest.hexdigest() != expected_sha:
        raise RunnerError("input_sha256 mismatch")


def download_harness_state(client: CallbackClient, destination: Path) -> bool:
    """Download the latest successful OpenCode state without buffering it."""
    total = 0
    req = Request(client.base + "/harness-state", headers=client.headers, method="GET")
    try:
        with urlopen(req, timeout=60) as response:
            if response.status == 204:
                return False
            with destination.open("xb") as output:
                while True:
                    chunk = response.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_HARNESS_STATE_BYTES:
                        raise RunnerError("OpenCode state archive exceeds size limit")
                    output.write(chunk)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:4000]
        raise RunnerError(
            f"callback GET /harness-state failed: {exc.code} {detail}"
        ) from exc
    except URLError as exc:
        raise RunnerError(f"callback GET /harness-state failed: {exc.reason}") from exc
    if total == 0:
        raise RunnerError("OpenCode state callback returned an empty archive")
    return True


def extract_zip(zip_path: Path, destination: Path, client: CallbackClient) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            raise RunnerError("input archive exceeds file count limit")
        expanded = 0
        seen: set[str] = set()
        for index, info in enumerate(infos, start=1):
            client.ensure_not_cancelled()
            pure, is_dir = _safe_member(info.filename)
            _validate_zip_info(info, is_dir)
            key = str(pure).casefold()
            if key in seen:
                raise RunnerError(f"duplicate archive member: {info.filename!r}")
            seen.add(key)
            if is_dir:
                destination.joinpath(*pure.parts).mkdir(parents=True, exist_ok=True)
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise RunnerError(f"archive member exceeds size limit: {info.filename}")
            target = destination.joinpath(*pure.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info) as source, target.open("xb") as output:
                while True:
                    chunk = source.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    expanded += len(chunk)
                    if written > MAX_FILE_BYTES or expanded > MAX_EXPANDED_BYTES:
                        raise RunnerError("input archive exceeds expanded size limit")
                    output.write(chunk)
            client.progress("extract", index, len(infos))


def build_commands(workspace: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return Bifrost-owned install/build commands for a staged app input."""
    package_json = workspace / "package.json"
    if not package_json.is_file():
        raise RunnerError("package.json is required for solution.build")
    try:
        package = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RunnerError("package.json must be valid JSON") from exc
    if not isinstance(package, dict) or package.get("private") is not True:
        raise RunnerError("package.json is not a Bifrost build input")
    meta_path = workspace / "build-meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RunnerError("build-meta.json is required and must be valid JSON") from exc
    base = meta.get("base") if isinstance(meta, dict) else None
    root_relative = (
        isinstance(base, str)
        and base.startswith("/")
        and not base.startswith("//")
    )
    if (
        not isinstance(base, str)
        or (base != "./" and not root_relative)
        or any(character in base for character in ("\x00", "\\", "?", "#"))
    ):
        raise RunnerError("build-meta.json contains an invalid base path")
    if not shutil.which("npm"):
        raise RunnerError("npm is unavailable in the runner image")
    return (
        ("npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"),
        (
            "npx",
            "--no-install",
            "vite",
            "build",
            "--config",
            "vite.config.mjs",
            "--base",
            base,
        ),
    )


def run_command(
    command: tuple[str, ...],
    workspace: Path,
    *,
    client: CallbackClient,
    deadline: float,
) -> str:
    env = {
        "CI": "true",
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, 0)
                client.ensure_not_cancelled()
                time.sleep(1)
        except (subprocess.TimeoutExpired, Cancelled) as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            output.seek(0)
            log = _tail(output.read())
            if isinstance(exc, Cancelled):
                raise Cancelled(exc.message, log_excerpt=log) from exc
            raise RunnerError("build timed out", log_excerpt=log) from exc
        output.seek(0)
        log = _tail(output.read())
    if process.returncode != 0:
        raise RunnerError(f"{command[0]} exited with status {process.returncode}", log_excerpt=log)
    return log


def run_build(client: CallbackClient, workspace: Path, timeout: int) -> str:
    deadline = time.monotonic() + timeout
    logs: list[str] = []
    for phase, command in zip(("install", "build"), build_commands(workspace), strict=True):
        client.ensure_not_cancelled()
        client.progress(phase)
        try:
            logs.append(run_command(command, workspace, client=client, deadline=deadline))
        except RunnerError as exc:
            combined = "\n".join(item for item in (*logs, exc.log_excerpt) if item)
            raise RunnerError(exc.message, log_excerpt=_tail(combined)) from exc
    return _tail("\n".join(logs))


def _tail(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return raw[-MAX_LOG_BYTES:].decode("utf-8", errors="replace")


def _opencode_exit_error(returncode: int, raw: bytes) -> RunnerError:
    """Turn the helper's bounded structured stderr into an actionable error."""
    log_excerpt = _tail(raw).strip()
    if not log_excerpt:
        return RunnerError(f"OpenCode exited with status {returncode}")
    # The SDK helper emits one final, sanitized diagnostic line. Keep the
    # owner-visible turn error within the callback contract while retaining
    # the longer excerpt for provider observability.
    detail = log_excerpt.splitlines()[-1].strip()
    harness_session_id = None
    harness_diagnostics = None
    try:
        diagnostic = json.loads(detail)
    except json.JSONDecodeError:
        diagnostic = None
    if isinstance(diagnostic, dict):
        error = diagnostic.get("error")
        if isinstance(error, str) and error.strip():
            detail = error.strip()
        session_id = diagnostic.get("harness_session_id")
        if isinstance(session_id, str) and session_id.strip():
            harness_session_id = session_id.strip()
        if isinstance(diagnostic.get("harness_diagnostics"), dict):
            harness_diagnostics = diagnostic["harness_diagnostics"]
    message = f"OpenCode turn failed: {detail}"[:4000]
    return RunnerError(
        message,
        log_excerpt=log_excerpt,
        harness_session_id=harness_session_id,
        harness_diagnostics=harness_diagnostics,
    )


def _session_id_from_marker(marker_path: Path) -> str | None:
    """Read the trusted helper marker left before a long-running prompt."""
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(marker, dict) or set(marker) != {"schema_version", "session_id"}:
        return None
    session_id = marker.get("session_id")
    if marker.get("schema_version") != 1 or not isinstance(session_id, str):
        return None
    return session_id.strip() or None


def _attach_marker_session(error: RunnerError, marker_path: Path) -> RunnerError:
    if error.harness_session_id is None:
        error.harness_session_id = _session_id_from_marker(marker_path)
    return error


def _safe_output_path(path: Path, base: Path) -> str:
    rel = path.relative_to(base).as_posix()
    _safe_member(rel)
    return rel


def upload_dist(client: CallbackClient, workspace: Path) -> list[dict[str, Any]]:
    dist = workspace / "dist"
    if not dist.is_dir() or dist.is_symlink():
        raise RunnerError("build did not produce a dist directory")
    manifest: list[dict[str, Any]] = []
    total = 0
    files = sorted(path for path in dist.rglob("*") if path.is_file())
    if len(files) > MAX_FILES:
        raise RunnerError("build output exceeds file count limit")
    for index, path in enumerate(files, start=1):
        client.ensure_not_cancelled()
        if path.is_symlink():
            raise RunnerError(f"build output contains symlink: {path.name}")
        rel = _safe_output_path(path, dist)
        if path.stat().st_size > MAX_OUTPUT_BYTES - total:
            raise RunnerError("build output exceeds size limit")
        body = path.read_bytes()
        total += len(body)
        if total > MAX_OUTPUT_BYTES:
            raise RunnerError("build output exceeds size limit")
        response = client.request("PUT", f"/artifacts/{quote(rel)}", body=body)
        try:
            uploaded = json.loads(response)
        except json.JSONDecodeError as exc:
            raise RunnerError("artifact callback returned invalid JSON") from exc
        manifest.append({"path": rel, **uploaded})
        client.progress("upload", index, len(files))
    return manifest


def workspace_zip(workspace: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    files = sorted(item for item in workspace.rglob("*") if item.is_file())
    if len(files) > MAX_TOOL_WORKSPACE_FILES:
        raise RunnerError("workspace exceeds file count limit")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_symlink():
                raise RunnerError(f"workspace contains symlink: {path.name}")
            rel = _safe_output_path(path, workspace)
            info = zipfile.ZipInfo(rel, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            data = path.read_bytes()
            total += len(data)
            if len(data) > MAX_TOOL_FILE_BYTES or total > MAX_TOOL_WORKSPACE_BYTES:
                raise RunnerError("workspace exceeds byte limits")
            archive.writestr(info, data)
    if destination.stat().st_size > MAX_OUTPUT_BYTES:
        raise RunnerError("workspace archive exceeds output limit")
    with destination.open("rb") as source:
        while chunk := source.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def restore_harness_state(
    archive_path: Path,
    home: Path,
    client: CallbackClient,
) -> str:
    """Restore a runner-owned OpenCode state archive and return its session id."""
    extract_zip(archive_path, home, client)
    manifest_path = home / "harness.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RunnerError("OpenCode state archive has an invalid manifest") from exc
    manifest_path.unlink()
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "session_id",
    }:
        raise RunnerError("OpenCode state archive has an invalid manifest")
    if manifest.get("schema_version") != 1:
        raise RunnerError("OpenCode state archive uses an unsupported schema")
    session_id = manifest.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise RunnerError("OpenCode state archive has no session id")
    state_root = home / ".local" / "share" / "opencode"
    if not state_root.is_dir() or state_root.is_symlink():
        raise RunnerError("OpenCode state archive has no runtime state")
    return session_id.strip()


def harness_state_zip(home: Path, destination: Path, session_id: str) -> str:
    """Archive the closed OpenCode runtime state for the next Builder turn."""
    if not session_id.strip():
        raise RunnerError("OpenCode completion has no session id")
    state_root = home / ".local" / "share" / "opencode"
    if not state_root.is_dir() or state_root.is_symlink():
        raise RunnerError("OpenCode did not persist its session state")
    files = sorted(path for path in state_root.rglob("*") if path.is_file())
    if not files:
        raise RunnerError("OpenCode persisted an empty session state")
    if len(files) + 1 > MAX_FILES:
        raise RunnerError("OpenCode state exceeds file count limit")

    digest = hashlib.sha256()
    expanded = 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = json.dumps(
            {"schema_version": 1, "session_id": session_id.strip()},
            separators=(",", ":"),
        ).encode()
        info = zipfile.ZipInfo("harness.json", ZIP_TIMESTAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        archive.writestr(info, manifest)
        expanded += len(manifest)

        for path in files:
            if path.is_symlink():
                raise RunnerError("OpenCode state contains a symlink")
            rel = _safe_output_path(path, home)
            data = path.read_bytes()
            expanded += len(data)
            if len(data) > MAX_FILE_BYTES or expanded > MAX_EXPANDED_BYTES:
                raise RunnerError("OpenCode state exceeds expanded size limit")
            info = zipfile.ZipInfo(rel, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, data)

    if destination.stat().st_size > MAX_HARNESS_STATE_BYTES:
        raise RunnerError("OpenCode state archive exceeds size limit")
    with destination.open("rb") as source:
        while chunk := source.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _required_context_string(context: dict[str, Any], key: str) -> str:
    value = context.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RunnerError(f"Builder context {key} is required")
    return value.strip()


def _conversation_tail(context: dict[str, Any]) -> str:
    raw = context.get("messages")
    if not isinstance(raw, list):
        raise RunnerError("Builder context messages must be a list")
    prior: list[str] = []
    for item in raw[:-1][-20:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            prior.append(f"{str(role).upper()}: {content.strip()}")
    tail = "\n\n".join(prior)
    if len(tail) > 30_000:
        tail = tail[-30_000:]
    return tail


def _latest_user_prompt(context: dict[str, Any]) -> str:
    raw = context.get("messages")
    if not isinstance(raw, list):
        raise RunnerError("Builder context messages must be a list")
    for item in reversed(raw):
        if (
            isinstance(item, dict)
            and item.get("role") == "user"
            and isinstance(item.get("content"), str)
            and item["content"].strip()
        ):
            return item["content"].strip()
    raise RunnerError("Builder context has no user prompt")


def opencode_config(
    client: CallbackClient,
    context: dict[str, Any],
    *,
    restored_session: bool = False,
) -> dict[str, Any]:
    model = _required_context_string(context, "model")
    system_prompt = _required_context_string(context, "system_prompt")
    bundle_path = context.get("bundle_path")
    if isinstance(bundle_path, str) and bundle_path.strip():
        bundle_path = bundle_path.strip().rstrip("/")
        system_prompt += (
            "\n\n## Native Agent Skill\n\n"
            f"These instructions are sourced from `{bundle_path}/SKILL.md`. "
            f"Read supporting files under `{bundle_path}/` when the instructions "
            "reference them."
        )
    history = _conversation_tail(context)
    if history and not restored_session:
        system_prompt += (
            "\n\n## Restored Builder conversation\n\n"
            "Use this bounded tail only as prior context; the current user request is "
            "provided separately.\n\n" + history
        )
    steps = max(1, min(200, int(context.get("max_iterations") or 50)))
    return {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "autoupdate": False,
        "enabled_providers": ["bifrost"],
        "model": f"bifrost/{model}",
        "small_model": f"bifrost/{model}",
        "default_agent": "bifrost-builder",
        "provider": {
            "bifrost": {
                "name": "Bifrost metered Builder gateway",
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "apiKey": client.envelope.capability,
                    "baseURL": client.base + "/llm/v1",
                    "timeout": OPENCODE_PROVIDER_TIMEOUT_MS,
                    "headers": {"User-Agent": RUNNER_USER_AGENT},
                },
                "models": {
                    model: {
                        "tool_call": True,
                        "limit": {
                            "context": OPENCODE_CONTEXT_WINDOW,
                            "output": OPENCODE_OUTPUT_LIMIT,
                        },
                    }
                },
            }
        },
        "agent": {
            "bifrost-builder": {
                "mode": "primary",
                "model": f"bifrost/{model}",
                "steps": steps,
                "prompt": system_prompt + "\n\n" + NATIVE_BUILDER_RUNTIME_CONTRACT,
                "permission": {
                    "read": "allow",
                    "edit": "allow",
                    "glob": "allow",
                    "grep": "allow",
                    "list": "allow",
                    "bash": {
                        "*": "allow",
                        "*npm run dev*": "deny",
                        "*npm start*": "deny",
                        "*pnpm dev*": "deny",
                        "*yarn dev*": "deny",
                        "*bun dev*": "deny",
                        "*--watch*": "deny",
                        "*python*http.server*": "deny",
                        "*uvicorn*": "deny",
                        "*flask run*": "deny",
                    },
                    "task": "allow",
                    "external_directory": "deny",
                    "todowrite": "allow",
                    "todoread": "allow",
                    "question": "deny",
                    "webfetch": "deny",
                    "websearch": "deny",
                    "codesearch": "deny",
                    "lsp": "allow",
                    "doom_loop": "deny",
                    "skill": "allow",
                },
            }
        },
        "compaction": {"auto": True, "prune": True},
        "experimental": {"chatMaxRetries": 2},
    }


def run_opencode_turn(
    client: CallbackClient,
    workspace: Path,
    context: dict[str, Any],
    timeout: int,
    *,
    home: Path,
    restored_session_id: str | None,
) -> dict[str, Any]:
    if not shutil.which("opencode") or not shutil.which("node"):
        raise RunnerError("OpenCode or Node.js is unavailable in the runner image")
    if not OPENCODE_HELPER.is_file():
        raise RunnerError("The typed OpenCode SDK helper is unavailable")
    prompt = _latest_user_prompt(context)
    model = _required_context_string(context, "model")
    request_path = workspace.parent / "opencode-request.json"
    session_marker_path = workspace.parent / "opencode-session.json"
    request_path.write_text(
        json.dumps(
            {
                "config": opencode_config(
                    client,
                    context,
                    restored_session=restored_session_id is not None,
                ),
                "directory": str(workspace),
                "prompt": prompt,
                "model": model,
                "title": f"Bifrost Builder {client.envelope.job_id}",
                "sessionID": restored_session_id,
                "sessionMarkerPath": str(session_marker_path),
                "timeoutSeconds": timeout,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    request_path.chmod(0o600)
    env = {
        "CI": "true",
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "TMPDIR": str(workspace.parent),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "OPENCODE_DISABLE_AUTOUPDATE": "true",
        "OPENCODE_DISABLE_TERMINAL_TITLE": "true",
    }
    command = (
        "node",
        str(OPENCODE_HELPER),
        str(request_path),
    )
    deadline = time.monotonic() + timeout
    client.progress("harness", 0, int(context.get("max_iterations") or 50))
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                if os.fstat(output.fileno()).st_size > MAX_HARNESS_OUTPUT_BYTES:
                    raise RunnerError("OpenCode helper output exceeds the runner limit")
                client.ensure_not_cancelled()
                time.sleep(1)
        except (subprocess.TimeoutExpired, Cancelled, RunnerError) as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            output.seek(0)
            log = _tail(output.read())
            if isinstance(exc, Cancelled):
                raise _attach_marker_session(
                    Cancelled(exc.message, log_excerpt=log),
                    session_marker_path,
                ) from exc
            if isinstance(exc, RunnerError):
                error = RunnerError(
                    exc.message,
                    log_excerpt=log,
                    harness_session_id=exc.harness_session_id,
                    harness_diagnostics=exc.harness_diagnostics,
                )
                raise _attach_marker_session(error, session_marker_path) from exc
            raise _attach_marker_session(
                RunnerError("OpenCode turn timed out", log_excerpt=log),
                session_marker_path,
            ) from exc
        output.seek(0)
        raw = output.read(MAX_HARNESS_OUTPUT_BYTES + 1)
    if len(raw) > MAX_HARNESS_OUTPUT_BYTES:
        raise RunnerError("OpenCode helper output exceeds the runner limit")
    if process.returncode != 0:
        raise _attach_marker_session(
            _opencode_exit_error(process.returncode, raw),
            session_marker_path,
        )
    try:
        completion = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RunnerError(
            "OpenCode SDK helper returned invalid JSON",
            log_excerpt=_tail(raw),
        ) from exc
    if not isinstance(completion, dict):
        raise RunnerError("OpenCode SDK helper returned a non-object result")
    required = {
        "status",
        "final_text",
        "tool_call_count",
        "model",
        "token_count_input",
        "token_count_output",
        "harness_session_id",
        "harness_diagnostics",
    }
    if set(completion) != required or completion.get("status") != "succeeded":
        raise RunnerError("OpenCode SDK helper returned an invalid completion")
    if not isinstance(completion.get("harness_session_id"), str):
        raise RunnerError("OpenCode SDK helper returned no session id")
    return completion


def stage_turn_artifacts(
    client: CallbackClient,
    *,
    scratch: Path,
    workspace: Path,
    home: Path,
    harness_session_id: str,
) -> str:
    """Upload one closed harness state and its matching workspace archive."""
    state_output = scratch / "harness-output.zip"
    harness_state_zip(home, state_output, harness_session_id)
    client.request(
        "PUT",
        "/harness-state",
        body=state_output.read_bytes(),
        timeout=120,
    )
    output_zip = scratch / "turn-output.zip"
    output_sha256 = workspace_zip(workspace, output_zip)
    client.request("PUT", "/output", body=output_zip.read_bytes(), timeout=120)
    return output_sha256


def _checkpoint_after_interruption(
    client: CallbackClient,
    *,
    scratch: Path,
    workspace: Path,
    home: Path | None,
    error: RunnerError,
) -> str | None:
    """Stage an inert checkpoint when a turn reached a durable harness session."""
    if home is None or error.harness_session_id is None:
        return None
    try:
        return stage_turn_artifacts(
            client,
            scratch=scratch,
            workspace=workspace,
            home=home,
            harness_session_id=error.harness_session_id,
        )
    except RunnerError as checkpoint_error:
        print(
            f"Builder checkpoint could not be preserved: {checkpoint_error.message}",
            file=sys.stderr,
        )
        error.message = (
            f"{error.message} Checkpoint could not be preserved: "
            f"{checkpoint_error.message}"
        )[:4000]
        return None


def run(envelope: Envelope, work_root: Path) -> int:
    client = CallbackClient(envelope)
    scratch = Path(tempfile.mkdtemp(prefix="job-", dir=work_root))
    input_zip = scratch / "input.zip"
    workspace = scratch / "workspace"
    workspace.mkdir()
    turn_home: Path | None = None
    try:
        client.progress("starting")
        download_input(client, input_zip, envelope.input_sha256)
        extract_zip(input_zip, workspace, client)
        if envelope.job_type == "solution.build":
            log = run_build(client, workspace, envelope.timeout_seconds)
            manifest = upload_dist(client, workspace)
            client.complete_build("succeeded", manifest=manifest, log_excerpt=log)
        else:
            context = client.get_json("/context")
            turn_home = scratch / "opencode-home"
            turn_home.mkdir(mode=0o700)
            state_input = scratch / "harness-input.zip"
            restored_session_id = None
            if download_harness_state(client, state_input):
                restored_session_id = restore_harness_state(
                    state_input,
                    turn_home,
                    client,
                )
            completion = run_opencode_turn(
                client,
                workspace,
                context,
                envelope.timeout_seconds,
                home=turn_home,
                restored_session_id=restored_session_id,
            )
            harness_session_id = completion.pop("harness_session_id")
            assert isinstance(harness_session_id, str)
            completion["output_sha256"] = stage_turn_artifacts(
                client,
                scratch=scratch,
                workspace=workspace,
                home=turn_home,
                harness_session_id=harness_session_id,
            )
            client.complete_turn(completion)
        return 0
    except Cancelled as exc:
        try:
            if envelope.job_type == "solution.build":
                client.complete_build(
                    "cancelled",
                    error=exc.message,
                    log_excerpt=exc.log_excerpt,
                )
            else:
                body: dict[str, Any] = {
                    "status": "cancelled",
                    "error": exc.message,
                }
                checkpoint_sha256 = _checkpoint_after_interruption(
                    client,
                    scratch=scratch,
                    workspace=workspace,
                    home=turn_home,
                    error=exc,
                )
                if checkpoint_sha256 is not None:
                    body["checkpoint_output_sha256"] = checkpoint_sha256
                if exc.harness_diagnostics is not None:
                    body["harness_diagnostics"] = exc.harness_diagnostics
                client.complete_turn(body)
        except RunnerError as callback_error:
            print(callback_error.message, file=sys.stderr)
            return CALLBACK_FAILURE_EXIT
        return REPORTED_CANCELLED_EXIT
    except RunnerError as exc:
        if exc.log_excerpt:
            print(exc.log_excerpt, file=sys.stderr)
        try:
            if envelope.job_type == "solution.build":
                client.complete_build(
                    "failed",
                    error=exc.message,
                    log_excerpt=exc.log_excerpt,
                )
            else:
                body: dict[str, Any] = {"status": "failed", "error": exc.message}
                checkpoint_sha256 = _checkpoint_after_interruption(
                    client,
                    scratch=scratch,
                    workspace=workspace,
                    home=turn_home,
                    error=exc,
                )
                body["error"] = exc.message
                if checkpoint_sha256 is not None:
                    body["checkpoint_output_sha256"] = checkpoint_sha256
                if exc.harness_diagnostics is not None:
                    body["harness_diagnostics"] = exc.harness_diagnostics
                client.complete_turn(body)
        except RunnerError as callback_error:
            print(callback_error.message, file=sys.stderr)
            return CALLBACK_FAILURE_EXIT
        return REPORTED_FAILURE_EXIT
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("envelope_file", nargs="?", help="Path to a JSON envelope.")
    parser.add_argument("--envelope", help="JSON envelope. Defaults to stdin.")
    parser.add_argument("--probe", action="store_true", help="Verify the fixed harness image.")
    args = parser.parse_args(argv)
    if args.probe:
        if args.envelope or args.envelope_file:
            parser.error("--probe cannot be combined with an envelope")
        opencode = shutil.which("opencode")
        node = shutil.which("node")
        if opencode is None or node is None or not OPENCODE_HELPER.is_file():
            print("OpenCode SDK harness is unavailable in the runner image", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "ready": True,
                    "schema_version": SCHEMA_VERSION,
                    "harness": "opencode",
                }
            )
        )
        return 0
    if args.envelope and args.envelope_file:
        parser.error("provide an envelope path, --envelope, or stdin, not multiple")
    if args.envelope_file:
        path = Path(args.envelope_file)
        if path.stat().st_size > MAX_ENVELOPE_BYTES:
            print("envelope exceeds size limit", file=sys.stderr)
            return 1
        raw = path.read_bytes()
    else:
        raw = args.envelope.encode() if args.envelope else sys.stdin.buffer.read(MAX_ENVELOPE_BYTES + 1)
    if len(raw) > MAX_ENVELOPE_BYTES:
        print("envelope exceeds size limit", file=sys.stderr)
        return 1
    try:
        envelope = Envelope.parse(raw)
        root = Path(os.environ.get("BIFROST_RUNNER_WORKDIR", "/work"))
        root.mkdir(parents=True, exist_ok=True)
        return run(envelope, root)
    except RunnerError as exc:
        print(exc.message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
