"""Least-privilege, file-backed App compilation stages for a launcher.

This module deliberately has no database, Redis, object-store, SDK-resolver or
provider imports.  The API owns the durable PlatformJob; a launcher only holds
the short-lived, fenced compute hand-off until the API releases it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from bifrost.app_build_identity import build_recipe, valid_recipe
from bifrost.app_lockfile import ReproducibleDependencyBuildError
from shared.app_build_compute import (
    MAX_INPUT_TRANSFER_BYTES,
    MAX_RESULT_TRANSFER_BYTES,
    decode_app_build_inputs,
    encode_app_build_result,
)
from shared.app_build_errors import AppBuildMemoryError, subprocess_memory_error
from shared.solution_app_compiler import AppBuildInputs, compile_app, output_tail
from shared.subprocess_env import package_tool_env
from shared.worker_transfer import (
    FileTransferChunk,
    FileTransferError,
    FileTransferManifest,
    FileTransferReceiver,
    ReceivedFileTransfer,
)
from src.services.execution.cpu_sampler import CPUSampler, get_clock_ticks


MAX_ACTIVE_STAGES = 4
# Tombstones are tiny identity-only records. A launcher drains before this
# ledger becomes an unbounded lifetime allocation, rather than reusing a
# released stage ID or silently refusing normal sequential workload.
MAX_STAGE_RECORDS = 4096
MAX_RECEIPT_BYTES = 64 * 1024


class _InputStream(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...


class _OutputStream(Protocol):
    async def read(self, n: int = -1) -> bytes: ...


class _Process(Protocol):
    @property
    def pid(self) -> int | None: ...

    @property
    def returncode(self) -> int | None: ...

    @property
    def stdin(self) -> _InputStream | None: ...

    @property
    def stdout(self) -> _OutputStream | None: ...

    async def wait(self) -> int: ...


Spawn = Callable[..., Awaitable[_Process]]
Kill = Callable[[_Process], Awaitable[None]]
TerminalNotification = Callable[[dict[str, str]], Awaitable[None]]


@dataclass(frozen=True)
class _Binding:
    stage_id: str
    job_id: str
    lease_token: str


@dataclass
class _Stage:
    binding: _Binding
    manifest: FileTransferManifest
    directory: Path
    receiver: FileTransferReceiver
    state: str = "prepared"
    input_transfer: ReceivedFileTransfer | None = None
    process: _Process | None = None
    task: asyncio.Task[None] | None = None
    result_manifest: FileTransferManifest | None = None
    build_recipe: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    sampler: CPUSampler | None = None
    output_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.output_path = self.directory / "result.zip"


@dataclass(frozen=True)
class _Tombstone:
    binding: _Binding


def _uuid(value: object, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _binding(payload: Mapping[str, Any]) -> _Binding:
    return _Binding(
        stage_id=_uuid(payload.get("stage_id"), "stage_id"),
        job_id=_uuid(payload.get("job_id"), "job_id"),
        lease_token=_uuid(payload.get("lease_token"), "lease_token"),
    )


def _manifest(value: object, *, max_total_size: int) -> FileTransferManifest:
    if not isinstance(value, Mapping):
        raise ValueError("App compile input manifest is required")
    return FileTransferManifest.from_dict(value, max_total_size=max_total_size)


async def _spawn_child(*args: Any, **kwargs: Any) -> _Process:
    return await asyncio.create_subprocess_exec(*args, **kwargs)


async def _kill_process_group(process: _Process) -> None:
    if process.returncode is not None or process.pid is None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


class AppCompileStages:
    """Own local compile processes and transfer files, never job authority."""

    def __init__(
        self,
        *,
        temp_dir: Path | None = None,
        spawn: Spawn = _spawn_child,
        kill: Kill = _kill_process_group,
        terminal_notification: TerminalNotification | None = None,
        max_active: int = MAX_ACTIVE_STAGES,
        max_records: int = MAX_STAGE_RECORDS,
    ) -> None:
        if max_active < 1 or max_records < max_active:
            raise ValueError("Invalid App compile stage limits")
        self._temp_dir = temp_dir
        self._spawn, self._kill = spawn, kill
        self._terminal_notification = terminal_notification
        self._max_active, self._max_records = max_active, max_records
        self._stages: dict[str, _Stage] = {}
        # A released ID remains fenced for the launcher lifetime.  The record
        # cap bounds this tiny identity-only journal as well as active stages.
        self._tombstones: dict[str, _Tombstone] = {}
        self._retire_requested = False

    @staticmethod
    def _same_binding(actual: _Binding, expected: _Binding) -> None:
        if actual != expected:
            raise ValueError(
                "App compile stage identity does not match its existing binding"
            )

    def prepare(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        binding = _binding(payload)
        input_value = payload.get("input")
        if not isinstance(input_value, Mapping):
            raise ValueError("App compile input is required")
        manifest = _manifest(input_value.get("manifest"), max_total_size=MAX_INPUT_TRANSFER_BYTES)
        if manifest.kind != "app_build_inputs":
            raise ValueError("App compile input must be an input transfer")
        tombstone = self._tombstones.get(binding.stage_id)
        if tombstone is not None:
            self._same_binding(tombstone.binding, binding)
            raise ValueError("App compile stage has already been released")
        existing = self._stages.get(binding.stage_id)
        if existing is not None:
            self._same_binding(existing.binding, binding)
            if existing.manifest != manifest:
                raise ValueError(
                    "App compile input manifest conflicts with the prepared stage"
                )
            return self._prepared_response(existing)
        if self._retire_requested:
            raise RuntimeError(
                "App compile stage journal is full; launcher retirement is requested"
            )
        if len(self._stages) >= self._max_active:
            raise RuntimeError("App compile stage limit is reached")
        if len(self._stages) + len(self._tombstones) >= self._max_records:
            raise RuntimeError("App compile stage record limit is reached")
        directory = Path(
            tempfile.mkdtemp(
                prefix=f"bifrost-appcompile-{binding.stage_id}-", dir=self._temp_dir
            )
        )
        try:
            receiver = FileTransferReceiver(manifest, temp_dir=directory)
            stage = _Stage(
                binding=binding,
                manifest=manifest,
                directory=directory,
                receiver=receiver,
            )
            self._stages[binding.stage_id] = stage
            return self._prepared_response(stage)
        except BaseException:
            directory.rmdir()
            raise

    def add_chunk(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        stage = self._stage(payload)
        if stage.state != "prepared":
            raise ValueError("App compile input can only be written while prepared")
        chunk = payload.get("chunk")
        if not isinstance(chunk, Mapping):
            raise ValueError("App compile input chunk is required")
        try:
            stage.receiver.add_chunk(chunk)
        except FileTransferError as exc:
            raise ValueError(str(exc)) from exc
        return {
            **_binding_dict(stage.binding),
            "accepted": True,
            "input_next_index": stage.receiver._next_index,
        }

    async def start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        stage = self._stage(payload)
        if stage.state != "prepared":
            return self._status(stage)
        try:
            stage.input_transfer = stage.receiver.finish()
        except FileTransferError as exc:
            self._fail(stage, "buildfailure", str(exc))
            await self._notify_terminal(stage)
            return self._status(stage)
        stage.state = "running"  # Fence duplicate start before any await.
        instruction = {
            "input_path": str(stage.input_transfer.path),
            "input_manifest": stage.manifest.to_dict(),
            "output_path": str(stage.output_path),
        }
        try:
            stage.process = await self._spawn(
                sys.executable,
                "-m",
                "src.worker.remote.app_compile",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                env=package_tool_env(),
            )
        except Exception as exc:
            # Ambiguous spawn is terminal: never launch a second child for this
            # stage ID, even if a platform retry chooses another launcher.
            self._fail(
                stage, "buildfailure", f"App compile child could not start: {exc}"
            )
            await self._notify_terminal(stage)
            return self._status(stage)
        stage.sampler = CPUSampler(
            pid=stage.process.pid or -1, clock_ticks=get_clock_ticks()
        )
        stage.sampler.set_baseline(time.monotonic())
        stage.task = asyncio.create_task(self._wait(stage, instruction))
        return self._status(stage)

    async def _wait(
        self, stage: _Stage, instruction: dict[str, str | dict[str, object]]
    ) -> None:
        assert stage.process is not None
        try:
            stdout = await self._child_receipt(stage.process, instruction)
            receipt = json.loads(stdout)
            if not isinstance(receipt, dict):
                raise ValueError("App compile child returned an invalid receipt")
            if receipt.get("state") == "succeeded" and stage.process.returncode == 0:
                manifest = _manifest(
                    receipt.get("result_manifest"), max_total_size=MAX_RESULT_TRANSFER_BYTES
                )
                if (
                    manifest.kind != "app_build_result"
                    or not stage.output_path.is_file()
                    or stage.output_path.stat().st_size != manifest.total_size
                ):
                    raise ValueError(
                        "App compile child did not produce a valid result archive"
                    )
                recipe = receipt.get("build_recipe")
                if not valid_recipe(recipe):
                    raise ValueError(
                        "App compile child returned an invalid build recipe"
                    )
                stage.result_manifest, stage.build_recipe, stage.state = (
                    manifest,
                    recipe,
                    "succeeded",
                )
            else:
                error = receipt.get("error")
                if (
                    not isinstance(error, dict)
                    or not isinstance(error.get("category"), str)
                    or not isinstance(error.get("message"), str)
                ):
                    raise ValueError(
                        "App compile child failed without a structured error"
                    )
                self._fail(stage, error["category"], error["message"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail(stage, "buildfailure", str(exc))
        finally:
            if stage.sampler is not None:
                stage.sampler.sample(time.monotonic())
            await self._notify_terminal(stage)

    async def _notify_terminal(self, stage: _Stage) -> None:
        """Notify only after state is locally stable and retained for readback."""
        if self._terminal_notification is not None and stage.state in (
            "succeeded",
            "failed",
        ):
            await self._terminal_notification(_binding_dict(stage.binding))

    async def _child_receipt(
        self, process: _Process, instruction: dict[str, str | dict[str, object]]
    ) -> bytes:
        """Read a child receipt incrementally so it cannot become a log pipe."""
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(instruction, separators=(",", ":")).encode())
        await process.stdin.drain()
        process.stdin.close()
        receipt = bytearray()
        while True:
            chunk = await process.stdout.read(
                min(4096, MAX_RECEIPT_BYTES + 1 - len(receipt))
            )
            if not chunk:
                break
            receipt.extend(chunk)
            if len(receipt) > MAX_RECEIPT_BYTES:
                await self._kill(process)
                raise ValueError("App compile child returned an oversized receipt")
        await process.wait()
        return bytes(receipt)

    def status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        binding = _binding(payload)
        tombstone = self._tombstones.get(binding.stage_id)
        if tombstone is not None:
            self._same_binding(tombstone.binding, binding)
            return {**_binding_dict(binding), "state": "released", "released": True}
        stage = self._stages.get(binding.stage_id)
        if stage is None:
            # Readback after a lost prepare reply must not guess whether the
            # mutation landed.  The API may safely prepare only after this
            # explicit absent response; no identity is trusted or retained.
            return {
                **_binding_dict(binding),
                "state": "unknown",
                "input_next_index": 0,
            }
        self._same_binding(stage.binding, binding)
        return self._status(stage)

    def result_chunk(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        stage = self._stage(payload)
        if stage.state != "succeeded" or stage.result_manifest is None:
            raise ValueError("App compile result is not available")
        index = payload.get("index")
        if type(index) is not int or not 0 <= index < stage.result_manifest.chunk_count:
            raise ValueError("App compile result chunk index is invalid")
        with stage.output_path.open("rb") as archive:
            archive.seek(index * stage.result_manifest.chunk_size)
            data = archive.read(stage.result_manifest.chunk_size)
        return {
            **_binding_dict(stage.binding),
            "chunk": FileTransferChunk(
                index=index, data_b64=base64.b64encode(data).decode("ascii")
            ).to_dict(),
        }

    async def release(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        binding = _binding(payload)
        tombstone = self._tombstones.get(binding.stage_id)
        if tombstone is not None:
            self._same_binding(tombstone.binding, binding)
            return {**_binding_dict(binding), "released": True}
        stage = self._stages.get(binding.stage_id)
        if stage is None:
            raise ValueError("Unknown App compile stage")
        self._same_binding(stage.binding, binding)
        if stage.process is not None:
            await self._kill(stage.process)
        if stage.task is not None and not stage.task.done():
            stage.task.cancel()
            await asyncio.gather(stage.task, return_exceptions=True)
        stage.receiver.close()
        if stage.input_transfer is not None:
            stage.input_transfer.close()
        stage.output_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            stage.directory.rmdir()
        # A failed stop must remain observable and retriable. Forgetting the
        # stage before physical cleanup would make readback falsely absent.
        self._stages.pop(binding.stage_id, None)
        self._tombstones[binding.stage_id] = _Tombstone(binding)
        if len(self._tombstones) >= self._max_records:
            self._retire_requested = True
        return {**_binding_dict(binding), "released": True}

    async def shutdown(self) -> None:
        for stage in list(self._stages.values()):
            with contextlib.suppress(Exception):
                await self.release(_binding_dict(stage.binding))

    @property
    def has_live(self) -> bool:
        return any(stage.state == "running" for stage in self._stages.values())

    @property
    def has_stages(self) -> bool:
        """Whether a prepared or terminal-unreleased stage still owns files."""
        return bool(self._stages)

    @property
    def retire_requested(self) -> bool:
        return self._retire_requested

    def inventory(self) -> list[dict[str, Any]]:
        return [self._inventory(stage) for stage in self._stages.values()]

    def telemetry(self) -> list[dict[str, Any]]:
        records = []
        for stage in self._stages.values():
            sampler = stage.sampler
            if sampler is not None and stage.state == "running":
                sampler.sample(time.monotonic())
            records.append(
                {
                    "stage_id": stage.binding.stage_id,
                    "pid": stage.process.pid if stage.process is not None else None,
                    "state": stage.state,
                    "rss_bytes": sampler.last_process_rss_bytes if sampler else None,
                    "peak_memory_bytes": sampler.peak_process_rss_bytes
                    if sampler
                    else None,
                    "peak_cpu_cores": sampler.peak_cpu_cores if sampler else None,
                }
            )
        return records

    def _stage(self, payload: Mapping[str, Any]) -> _Stage:
        binding = _binding(payload)
        stage = self._stages.get(binding.stage_id)
        if stage is None:
            raise ValueError("Unknown App compile stage")
        self._same_binding(stage.binding, binding)
        return stage

    @staticmethod
    def _prepared_response(stage: _Stage) -> dict[str, Any]:
        return {
            **_binding_dict(stage.binding),
            "state": stage.state,
            "accepted": True,
            "input_next_index": stage.receiver._next_index,
        }

    @staticmethod
    def _fail(stage: _Stage, category: str, message: str) -> None:
        stage.state, stage.error = (
            "failed",
            {"category": category, "message": message[:4096]},
        )

    def _status(self, stage: _Stage) -> dict[str, Any]:
        response: dict[str, Any] = {
            **_binding_dict(stage.binding),
            "state": stage.state,
        }
        if stage.state == "prepared":
            response["input_next_index"] = stage.receiver._next_index
        if stage.process is not None:
            response["pid"] = stage.process.pid
        if stage.error is not None:
            response["error"] = stage.error
        if stage.result_manifest is not None:
            response["result_manifest"] = stage.result_manifest.to_dict()
        if stage.build_recipe is not None:
            response["build_recipe"] = stage.build_recipe
        return response

    def _inventory(self, stage: _Stage) -> dict[str, Any]:
        record = self._status(stage)
        record.pop("input_next_index", None)
        record["input_sha256"] = stage.manifest.sha256
        return record


def _binding_dict(binding: _Binding) -> dict[str, str]:
    return {
        "stage_id": binding.stage_id,
        "job_id": binding.job_id,
        "lease_token": binding.lease_token,
    }


def _compile_instruction(instruction: Mapping[str, object]) -> dict[str, object]:
    """Child entrypoint: run one compiler without inheriting platform env."""
    try:
        input_path, output_path = (
            Path(str(instruction["input_path"])),
            Path(str(instruction["output_path"])),
        )
        manifest = _manifest(instruction["input_manifest"], max_total_size=MAX_INPUT_TRANSFER_BYTES)
        if manifest.kind != "app_build_inputs":
            raise ValueError("App compile input must be an input transfer")
        environment = package_tool_env()
        transfer = ReceivedFileTransfer(manifest=manifest, path=input_path)
        inputs = decode_app_build_inputs(transfer, environment=environment)
        if not valid_recipe(inputs.recipe):
            raise ValueError("App compile input has an invalid requested recipe")
        actual_recipe = build_recipe(
            inputs.sdk_tarball,
            variant=inputs.recipe["variant"],
            environment=environment,
        )
        result = compile_app(
            AppBuildInputs(
                app_id=inputs.app_id,
                source_files=inputs.source_files,
                dependencies=inputs.dependencies,
                sdk_tarball=inputs.sdk_tarball,
                recipe=actual_recipe,
                environment=environment,
                require_lockfile=inputs.require_lockfile,
                package_name=inputs.package_name,
            )
        )
        transfer_out = encode_app_build_result(result)
        output_manifest = transfer_out.manifest
        try:
            os.replace(transfer_out.path, output_path)
        finally:
            transfer_out.close()
        return {
            "state": "succeeded",
            "result_manifest": output_manifest.to_dict(),
            "build_recipe": actual_recipe,
        }
    except ReproducibleDependencyBuildError as exc:
        return _error("lockfile", str(exc))
    except subprocess.TimeoutExpired as exc:
        return _error("timeout", str(exc) or "App compile timed out")
    except AppBuildMemoryError as exc:
        return _error("memory", str(exc))
    except MemoryError as exc:
        return _error("memory", str(exc) or "App compile ran out of memory")
    except Exception as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            memory = subprocess_memory_error(exc.returncode, exc.stderr, exc.stdout)
            if memory is not None:
                return _error("memory", str(memory))
            return _error("buildfailure", output_tail(exc))
        return _error("buildfailure", str(exc))


def _error(category: str, message: str) -> dict[str, object]:
    return {
        "state": "failed",
        "error": {"category": category, "message": message[:4096]},
    }


def main() -> None:
    instruction = json.load(sys.stdin)
    if not isinstance(instruction, dict):
        raise ValueError("App compile instruction must be an object")
    print(
        json.dumps(_compile_instruction(instruction), separators=(",", ":")), flush=True
    )


if __name__ == "__main__":
    main()
