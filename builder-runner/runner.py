#!/usr/bin/env python3
"""Provider-neutral Bifrost builder sandbox harness."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
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

import yaml

SCHEMA_VERSION = 1
MAX_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_EXPANDED_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 10_000
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
MAX_LOG_BYTES = 128 * 1024
MAX_TOOL_FILE_BYTES = 5 * 1024 * 1024
MAX_TOOL_READ_BYTES = 256 * 1024
MAX_TOOL_WORKSPACE_BYTES = 200 * 1024 * 1024
MAX_TOOL_WORKSPACE_FILES = 5_000
MAX_ENVELOPE_BYTES = 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
JOB_TYPES = {"solution.build", "solution.builder.turn"}


class RunnerError(Exception):
    def __init__(self, message: str, *, log_excerpt: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.log_excerpt = log_excerpt


class Cancelled(RunnerError):
    pass


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
        self.headers = {"Authorization": f"Bearer {envelope.capability}"}

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 30,
    ) -> bytes:
        headers = dict(self.headers)
        payload = body
        if json_body is not None:
            payload = json.dumps(json_body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        req = Request(self.base + path, data=payload, headers=headers, method=method)
        try:
            with urlopen(req, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:4000]
            raise RunnerError(f"callback {method} {path} failed: {exc.code} {detail}") from exc
        except URLError as exc:
            raise RunnerError(f"callback {method} {path} failed: {exc.reason}") from exc

    def get_json(self, path: str) -> dict[str, Any]:
        body = self.request("GET", path)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RunnerError(f"callback {path} did not return JSON") from exc
        if not isinstance(data, dict):
            raise RunnerError(f"callback {path} returned non-object JSON")
        return data

    def complete_llm(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = self.request("POST", "/llm/completions", json_body=payload, timeout=120)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RunnerError("LLM completion callback returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise RunnerError("LLM completion callback returned non-object JSON")
        return data

    def progress(self, phase: str, current: int = 0, total: int | None = None) -> None:
        payload: dict[str, Any] = {"phase": phase[:200], "current": max(0, current)}
        if total is not None:
            payload["total"] = max(0, total)
            payload["percent"] = 100.0 if total == 0 else min(100.0, current / total * 100)
        self.request("POST", "/progress", json_body=payload, timeout=10)

    def ensure_not_cancelled(self) -> None:
        data = self.get_json("/cancelled")
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
    if not isinstance(base, str) or not base.startswith("/") or "\x00" in base:
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


def _workspace_path(workspace: Path, rel: str) -> Path:
    pure, is_dir = _safe_member(rel)
    if is_dir:
        raise RunnerError("workspace tool paths must name files")
    current = workspace.resolve()
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise RunnerError("workspace path contains a symlink")
    target = workspace.joinpath(*pure.parts).resolve()
    base = workspace.resolve()
    if target != base and base not in target.parents:
        raise RunnerError("workspace path escapes sandbox")
    return target


def _workspace_usage(workspace: Path) -> tuple[int, int]:
    files = [path for path in workspace.rglob("*") if path.is_file()]
    if any(path.is_symlink() for path in files):
        raise RunnerError("workspace contains a symlink")
    return len(files), sum(path.stat().st_size for path in files)


def _write_workspace_file(workspace: Path, rel: str, content: str) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_TOOL_FILE_BYTES:
        raise RunnerError("file exceeds per-file byte limit")
    target = _workspace_path(workspace, rel)
    old_size = target.stat().st_size if target.is_file() else 0
    count, total = _workspace_usage(workspace)
    if not target.exists() and count + 1 > MAX_TOOL_WORKSPACE_FILES:
        raise RunnerError("workspace exceeds file count limit")
    if total - old_size + len(encoded) > MAX_TOOL_WORKSPACE_BYTES:
        raise RunnerError("workspace exceeds total byte limit")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _builder_tools(
    enabled_system_tools: object,
    bundle_path: str | None,
) -> list[dict[str, Any]]:
    path = {"type": "string", "description": "Workspace-relative POSIX path."}
    catalog: list[dict[str, Any]] = [
        {"name": "list_files", "description": "List every file in the Solution workspace.", "parameters": {"type": "object", "properties": {}}},
        {"name": "read_file", "description": "Read a UTF-8 workspace file.", "parameters": {"type": "object", "properties": {"path": path}, "required": ["path"]}},
        {"name": "search_text", "description": "Regex-search text files in the workspace.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "glob": {"type": "string", "default": "**/*"}}, "required": ["pattern"]}},
        {"name": "write_file", "description": "Create or replace a UTF-8 workspace file.", "parameters": {"type": "object", "properties": {"path": path, "content": {"type": "string"}}, "required": ["path", "content"]}},
        {"name": "apply_patch", "description": "Replace exact text in one workspace file.", "parameters": {"type": "object", "properties": {"path": path, "old_string": {"type": "string"}, "new_string": {"type": "string"}, "replace_all": {"type": "boolean", "default": False}}, "required": ["path", "old_string", "new_string"]}},
        {"name": "delete_file", "description": "Delete one workspace file.", "parameters": {"type": "object", "properties": {"path": path}, "required": ["path"]}},
        {"name": "make_directory", "description": "Create a workspace directory.", "parameters": {"type": "object", "properties": {"path": path}, "required": ["path"]}},
        {"name": "validate_solution", "description": "Validate the workspace's Bifrost Solution descriptor.", "parameters": {"type": "object", "properties": {}}},
    ]
    if not isinstance(enabled_system_tools, list) or not all(
        isinstance(name, str) for name in enabled_system_tools
    ):
        raise RunnerError("Builder context system_tools must be a list of names")
    definitions = {tool["name"]: tool for tool in catalog}
    unknown = sorted(set(enabled_system_tools) - definitions.keys())
    if unknown:
        raise RunnerError(f"Unsupported Builder system tools: {', '.join(unknown)}")
    tools = [
        definitions[name]
        for name in dict.fromkeys(enabled_system_tools)
    ]
    if bundle_path:
        tools.append({"name": "read_skill_asset", "description": "Read a relative file from this agent's skill bundle.", "parameters": {"type": "object", "properties": {"path": path}, "required": ["path"]}})
    return tools


def run_turn_loop(client: CallbackClient, workspace: Path) -> dict[str, Any]:
    context = client.get_json("/context")
    messages: list[dict[str, Any]] = []
    system_prompt = context.get("system_prompt")
    if isinstance(system_prompt, str) and system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    restored = context.get("messages")
    if isinstance(restored, list):
        messages.extend(item for item in restored if isinstance(item, dict))
    bundle_path = context.get("bundle_path")
    if not isinstance(bundle_path, str) or not bundle_path:
        bundle_path = None
    tools = _builder_tools(context.get("system_tools"), bundle_path)
    allowed_tools = {tool["name"] for tool in tools}
    final_text = ""
    tool_calls = 0
    input_tokens = 0
    output_tokens = 0
    model = context.get("model") if isinstance(context.get("model"), str) else None
    max_iterations = int(context.get("max_iterations") or 1)
    for iteration in range(1, max(1, min(max_iterations, 50)) + 1):
        client.ensure_not_cancelled()
        client.progress("llm", iteration, max_iterations)
        remaining = max(1, int(context.get("max_token_budget") or 100_000) - output_tokens)
        response = client.complete_llm(
            {"messages": messages, "tools": tools, "max_tokens": min(16_384, remaining)}
        )
        input_tokens += int(response.get("input_tokens") or 0)
        output_tokens += int(response.get("output_tokens") or 0)
        if isinstance(response.get("model"), str):
            model = response["model"]
        content = response.get("content")
        calls = response.get("tool_calls")
        if isinstance(content, str):
            final_text = content
        if not calls:
            break
        if not isinstance(calls, list):
            raise RunnerError("LLM completion returned invalid tool_calls")
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for call in calls:
            if not isinstance(call, dict):
                continue
            tool_calls += 1
            messages.append(
                _execute_workspace_tool(
                    workspace,
                    call,
                    allowed_tools=allowed_tools,
                    bundle_path=bundle_path,
                )
            )
    archive = workspace.parent / "turn-output.zip"
    output_sha = workspace_zip(workspace, archive)
    client.request("PUT", "/output", body=archive.read_bytes(), timeout=60)
    return {
        "status": "succeeded",
        "output_sha256": output_sha,
        "final_text": final_text,
        "tool_call_count": tool_calls,
        "model": model,
        "token_count_input": input_tokens,
        "token_count_output": output_tokens,
    }


def _execute_workspace_tool(
    workspace: Path,
    call: dict[str, Any],
    *,
    allowed_tools: set[str],
    bundle_path: str | None = None,
) -> dict[str, Any]:
    name = call.get("name")
    arguments = call.get("arguments")
    call_id = call.get("id")
    result: dict[str, Any]
    try:
        if not isinstance(name, str) or name not in allowed_tools:
            raise RunnerError(f"tool is not enabled for this Builder turn: {name}")
        if not isinstance(arguments, dict):
            raise RunnerError("tool arguments must be an object")
        if name == "write_file":
            path = arguments.get("path")
            content = arguments.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                raise RunnerError("write_file requires path and content")
            _write_workspace_file(workspace, path, content)
            result = {"ok": True}
        elif name == "read_file":
            path = arguments.get("path")
            if not isinstance(path, str):
                raise RunnerError("read_file requires path")
            target = _workspace_path(workspace, path)
            if not target.is_file() or target.is_symlink():
                raise RunnerError("file not found or not a regular file")
            data = target.read_bytes()
            result = {"content": data[:MAX_TOOL_READ_BYTES].decode("utf-8", errors="replace"), "truncated": len(data) > MAX_TOOL_READ_BYTES}
        elif name == "list_files":
            files = sorted(_safe_output_path(path, workspace) for path in workspace.rglob("*") if path.is_file() and not path.is_symlink())
            if len(files) > MAX_TOOL_WORKSPACE_FILES:
                raise RunnerError("workspace exceeds file count limit")
            result = {"files": files}
        elif name == "search_text":
            pattern = arguments.get("pattern")
            glob = arguments.get("glob", "**/*")
            if not isinstance(pattern, str) or not isinstance(glob, str):
                raise RunnerError("search_text requires pattern and optional glob")
            _safe_member(glob)
            try:
                regex = re.compile(pattern)
            except re.error as exc:
                raise RunnerError(f"invalid search pattern: {exc}") from exc
            matches: list[dict[str, object]] = []
            for target in sorted(workspace.rglob(glob)):
                if not target.is_file() or target.is_symlink() or target.stat().st_size > MAX_TOOL_READ_BYTES:
                    continue
                try:
                    lines = target.read_text(encoding="utf-8").splitlines()
                except (UnicodeDecodeError, OSError):
                    continue
                for line_number, line in enumerate(lines, start=1):
                    if regex.search(line):
                        matches.append({"path": _safe_output_path(target, workspace), "line_number": line_number, "line": line[:2000]})
                        if len(matches) >= 200:
                            break
                if len(matches) >= 200:
                    break
            result = {"matches": matches}
        elif name == "apply_patch":
            path = arguments.get("path")
            old = arguments.get("old_string")
            new = arguments.get("new_string")
            replace_all = arguments.get("replace_all", False)
            if not isinstance(path, str) or not isinstance(old, str) or not isinstance(new, str) or not isinstance(replace_all, bool) or not old:
                raise RunnerError("apply_patch requires path, old_string, and new_string")
            target = _workspace_path(workspace, path)
            text = target.read_text(encoding="utf-8")
            occurrences = text.count(old)
            if occurrences == 0:
                raise RunnerError("old_string not found in file")
            if occurrences > 1 and not replace_all:
                raise RunnerError(f"old_string matches {occurrences} times")
            _write_workspace_file(workspace, path, text.replace(old, new, -1 if replace_all else 1))
            result = {"ok": True, "replacements": occurrences if replace_all else 1}
        elif name == "delete_file":
            path = arguments.get("path")
            if not isinstance(path, str):
                raise RunnerError("delete_file requires path")
            target = _workspace_path(workspace, path)
            if not target.is_file() or target.is_symlink():
                raise RunnerError("file not found or not a regular file")
            target.unlink()
            result = {"ok": True}
        elif name == "make_directory":
            path = arguments.get("path")
            if not isinstance(path, str):
                raise RunnerError("make_directory requires path")
            target = _workspace_path(workspace, path)
            if target.exists() and not target.is_dir():
                raise RunnerError("path exists and is not a directory")
            target.mkdir(parents=True, exist_ok=True)
            result = {"ok": True}
        elif name == "validate_solution":
            descriptor = workspace / "bifrost.solution.yaml"
            try:
                parsed = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
            except (FileNotFoundError, UnicodeDecodeError, yaml.YAMLError) as exc:
                raise RunnerError(f"invalid bifrost.solution.yaml: {exc}") from exc
            if not isinstance(parsed, dict) or not all(isinstance(parsed.get(key), str) and parsed[key].strip() for key in ("slug", "name")):
                raise RunnerError("bifrost.solution.yaml requires non-empty slug and name")
            result = {"valid": True, "file_count": _workspace_usage(workspace)[0]}
        elif name == "read_skill_asset":
            path = arguments.get("path")
            if not isinstance(path, str) or bundle_path is None:
                raise RunnerError("read_skill_asset is unavailable")
            bundle_root = _workspace_path(workspace, f"{bundle_path}/SKILL.md").parent
            target = _workspace_path(bundle_root, path)
            if not target.is_file() or target.is_symlink():
                raise RunnerError("skill asset not found or not a regular file")
            data = target.read_bytes()
            if len(data) > 1024 * 1024:
                raise RunnerError("skill asset exceeds read limit")
            result = {"path": path, "content": data.decode("utf-8", errors="replace")}
        else:
            raise RunnerError(f"unsupported tool: {name}")
    except Exception as exc:
        result = {"error": str(exc)[:4000]}
    return {
        "role": "tool",
        "tool_call_id": call_id if isinstance(call_id, str) else "",
        "content": json.dumps(result, separators=(",", ":")),
    }


def run(envelope: Envelope, work_root: Path) -> int:
    client = CallbackClient(envelope)
    scratch = Path(tempfile.mkdtemp(prefix="job-", dir=work_root))
    input_zip = scratch / "input.zip"
    workspace = scratch / "workspace"
    workspace.mkdir()
    try:
        client.progress("starting")
        download_input(client, input_zip, envelope.input_sha256)
        extract_zip(input_zip, workspace, client)
        if envelope.job_type == "solution.build":
            log = run_build(client, workspace, envelope.timeout_seconds)
            manifest = upload_dist(client, workspace)
            client.complete_build("succeeded", manifest=manifest, log_excerpt=log)
        else:
            completion = run_turn_loop(client, workspace)
            client.complete_turn(completion)
        return 0
    except Cancelled as exc:
        if envelope.job_type == "solution.build":
            client.complete_build("cancelled", error=exc.message, log_excerpt=exc.log_excerpt)
        else:
            client.complete_turn({"status": "cancelled", "error": exc.message})
        return 2
    except RunnerError as exc:
        if envelope.job_type == "solution.build":
            client.complete_build("failed", error=exc.message, log_excerpt=exc.log_excerpt)
        else:
            client.complete_turn({"status": "failed", "error": exc.message})
        return 1
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
        print(json.dumps({"ready": True, "schema_version": SCHEMA_VERSION}))
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
