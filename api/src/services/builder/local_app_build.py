"""Canonical npm/Vite build implementation used by the existing Worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.services.builder.fs_tools import WorkspaceLimits, safe_extract_zip

if TYPE_CHECKING:
    from src.services.builder.staged_artifacts import StagedBuildArtifactStorage

MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_EXPANDED_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 10_000
_CHUNK_BYTES = 1024 * 1024

ProgressReporter = Callable[[str, int, int | None], Awaitable[None]]
CancellationCheck = Callable[[], Awaitable[bool]]


class LocalBuildError(RuntimeError):
    def __init__(self, message: str, *, log_excerpt: str = "") -> None:
        super().__init__(message)
        self.log_excerpt = log_excerpt


class LocalBuildCancelled(LocalBuildError):
    pass


class LocalBuildTimeout(LocalBuildError):
    pass


def build_commands(workspace: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return fixed Bifrost-owned install and build commands."""
    package_json = workspace / "package.json"
    if not package_json.is_file():
        raise LocalBuildError("package.json is required for solution.build")
    try:
        package = json.loads(package_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LocalBuildError("package.json must be valid JSON") from exc
    if not isinstance(package, dict) or package.get("private") is not True:
        raise LocalBuildError("package.json is not a Bifrost build input")

    try:
        meta = json.loads((workspace / "build-meta.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LocalBuildError(
            "build-meta.json is required and must be valid JSON"
        ) from exc
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
        raise LocalBuildError("build-meta.json contains an invalid base path")
    if not shutil.which("npm"):
        raise LocalBuildError("npm is unavailable in the Worker image")
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


async def materialize_build_input(
    storage: StagedBuildArtifactStorage,
    destination: Path,
    *,
    expected_sha256: str,
) -> None:
    """Download, hash, bound, and safely extract one staged build input."""
    archive = destination.parent / "input.zip"
    digest = hashlib.sha256()
    total = 0
    with archive.open("xb") as output:
        async for chunk in storage.open_input_stream():
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                raise LocalBuildError("Build input archive exceeds the byte limit")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected_sha256:
        raise LocalBuildError("Build input SHA-256 does not match its durable record")
    safe_extract_zip(
        archive,
        destination,
        WorkspaceLimits(
            max_files=MAX_FILES,
            max_file_bytes=MAX_FILE_BYTES,
            max_total_bytes=MAX_EXPANDED_BYTES,
        ),
    )


async def run_local_app_build(
    *,
    workspace: Path,
    storage: StagedBuildArtifactStorage,
    app_id: UUID,
    timeout_seconds: int,
    log_limit_bytes: int,
    output_limit_bytes: int,
    report: ProgressReporter,
    is_cancelled: CancellationCheck,
) -> tuple[list[dict[str, Any]], str]:
    """Run the canonical build and stage its immutable ``dist`` manifest."""
    deadline = time.monotonic() + timeout_seconds
    log = bytearray()
    for phase, command in zip(
        ("Installing application dependencies", "Compiling application"),
        build_commands(workspace),
        strict=True,
    ):
        await report(phase, 0, None)
        await _run_command(
            command,
            workspace,
            deadline=deadline,
            log=log,
            log_limit_bytes=log_limit_bytes,
            is_cancelled=is_cancelled,
        )

    dist = workspace / "dist"
    if not dist.is_dir() or dist.is_symlink():
        raise LocalBuildError(
            "Build did not produce a dist directory",
            log_excerpt=_decode_log(log),
        )
    files = sorted(path for path in dist.rglob("*") if path.is_file())
    if len(files) > MAX_FILES:
        raise LocalBuildError(
            "Build output exceeds the file-count limit",
            log_excerpt=_decode_log(log),
        )

    manifest: list[dict[str, Any]] = []
    for index, path in enumerate(files, start=1):
        if await is_cancelled():
            raise LocalBuildCancelled(
                "Build was cancelled",
                log_excerpt=_decode_log(log),
            )
        if path.is_symlink():
            raise LocalBuildError(
                f"Build output contains a symlink: {path.name}",
                log_excerpt=_decode_log(log),
            )
        rel_path = path.relative_to(dist).as_posix()
        digest, size = await storage.write_output(
            app_id,
            rel_path,
            _file_chunks(path),
            output_limit_bytes,
        )
        manifest.append({"path": rel_path, "sha256": digest, "size": size})
        await report("Staging compiled application", index, len(files))
    if not manifest:
        raise LocalBuildError(
            "Build produced no files",
            log_excerpt=_decode_log(log),
        )
    return manifest, _decode_log(log)


async def _run_command(
    command: tuple[str, ...],
    workspace: Path,
    *,
    deadline: float,
    log: bytearray,
    log_limit_bytes: int,
    is_cancelled: CancellationCheck,
) -> None:
    runtime_root = workspace.parent / "runtime"
    home = runtime_root / "home"
    cache = runtime_root / "npm-cache"
    tmp = runtime_root / "tmp"
    for directory in (home, cache, tmp):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=workspace,
        env={
            "CI": "true",
            "HOME": str(home),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "TMPDIR": str(tmp),
            "npm_config_cache": str(cache),
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
        },
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdout is not None
    drain = asyncio.create_task(
        _drain_output(process.stdout, log, log_limit_bytes=log_limit_bytes)
    )
    wait_task = asyncio.create_task(process.wait())
    try:
        while process.returncode is None:
            if await is_cancelled():
                await _terminate(process)
                raise LocalBuildCancelled(
                    "Build was cancelled",
                    log_excerpt=_decode_log(log),
                )
            if time.monotonic() >= deadline:
                await _terminate(process)
                raise LocalBuildTimeout(
                    "Build timed out",
                    log_excerpt=_decode_log(log),
                )
            try:
                await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.5)
            except TimeoutError:
                continue
    except BaseException:
        await _terminate(process)
        raise
    finally:
        if not wait_task.done():
            await wait_task
        await drain
    if process.returncode != 0:
        raise LocalBuildError(
            f"{command[0]} exited with status {process.returncode}",
            log_excerpt=_decode_log(log),
        )


async def _drain_output(
    stream: asyncio.StreamReader,
    log: bytearray,
    *,
    log_limit_bytes: int,
) -> None:
    while chunk := await stream.read(64 * 1024):
        log.extend(chunk)
        overflow = len(log) - log_limit_bytes
        if overflow > 0:
            del log[:overflow]


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def _file_chunks(path: Path):
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_BYTES):
            yield chunk
            await asyncio.sleep(0)


def _decode_log(log: bytearray) -> str:
    return bytes(log).decode("utf-8", errors="replace")


__all__ = [
    "LocalBuildCancelled",
    "LocalBuildError",
    "LocalBuildTimeout",
    "build_commands",
    "materialize_build_input",
    "run_local_app_build",
]
