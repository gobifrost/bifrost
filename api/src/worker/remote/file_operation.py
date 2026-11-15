"""Code-registered, file-backed compute stages for a physical launcher.

The API remains authoritative for durable work. This module only retains a
fenced file transfer and the short-lived child process that consumes it. A
single registry owns admission and tombstones for every named operation a
launcher supports; operation names are selected by local code, never requests.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import signal
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from shared.worker_transfer import (
    FileTransferChunk,
    FileTransferError,
    FileTransferManifest,
    FileTransferReceiver,
    ReceivedFileTransfer,
)
from src.services.execution.cpu_sampler import CPUSampler, get_clock_ticks


MAX_ACTIVE_STAGES = 4
MAX_STAGE_RECORDS = 4096
MAX_RECEIPT_BYTES = 64 * 1024
DISK_HEADROOM_BYTES = 16 * 1024 * 1024


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
class FileOperationBinding:
    stage_id: str
    job_id: str
    lease_token: str


@dataclass(frozen=True)
class FileOperationResult:
    manifest: FileTransferManifest
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FileOperationChild:
    args: tuple[str, ...]
    kwargs: Mapping[str, Any]


@dataclass(frozen=True)
class FileOperationDefinition:
    """A locally registered operation recipe, never request-controlled code."""

    name: str
    label: str
    input_max_bytes: int
    validate_input: Callable[[FileTransferManifest], None]
    child_instruction: Callable[["FileOperationStage"], dict[str, object]]
    decode_success: Callable[[Mapping[str, Any], "FileOperationStage"], FileOperationResult]
    child: Callable[[], FileOperationChild]
    output_name: str
    temp_prefix: str
    workspace_max_bytes: int = 0


@dataclass
class FileOperationStage:
    operation: FileOperationDefinition
    binding: FileOperationBinding
    manifest: FileTransferManifest
    directory: Path
    receiver: FileTransferReceiver
    state: str = "prepared"
    input_transfer: ReceivedFileTransfer | None = None
    process: _Process | None = None
    task: asyncio.Task[None] | None = None
    result: FileOperationResult | None = None
    error: dict[str, str] | None = None
    sampler: CPUSampler | None = None
    output_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.output_path = self.directory / self.operation.output_name


@dataclass(frozen=True)
class _Tombstone:
    operation_name: str
    binding: FileOperationBinding


def _uuid(value: object, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def binding_from_payload(payload: Mapping[str, Any]) -> FileOperationBinding:
    return FileOperationBinding(
        stage_id=_uuid(payload.get("stage_id"), "stage_id"),
        job_id=_uuid(payload.get("job_id"), "job_id"),
        lease_token=_uuid(payload.get("lease_token"), "lease_token"),
    )


def binding_dict(binding: FileOperationBinding) -> dict[str, str]:
    return {
        "stage_id": binding.stage_id,
        "job_id": binding.job_id,
        "lease_token": binding.lease_token,
    }


async def spawn_child(*args: Any, **kwargs: Any) -> _Process:
    return await asyncio.create_subprocess_exec(*args, **kwargs)


async def kill_process_group(process: _Process) -> None:
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


class NamedFileOperationStages:
    """One shared admission journal for code-whitelisted file operations."""

    def __init__(
        self,
        *,
        operations: Mapping[str, FileOperationDefinition],
        temp_dir: Path | None = None,
        spawn: Spawn = spawn_child,
        kill: Kill = kill_process_group,
        terminal_notification: TerminalNotification | None = None,
        max_active: int = MAX_ACTIVE_STAGES,
        max_records: int = MAX_STAGE_RECORDS,
    ) -> None:
        if max_active < 1 or max_records < max_active:
            raise ValueError("Invalid file operation stage limits")
        if not operations:
            raise ValueError("File operation registry cannot be empty")
        self._operations = dict(operations)
        if any(name != operation.name for name, operation in self._operations.items()):
            raise ValueError("File operation registry key does not match operation name")
        if any(
            type(operation.workspace_max_bytes) is not int or operation.workspace_max_bytes < 0
            for operation in self._operations.values()
        ):
            raise ValueError("File operation workspace bound must be a nonnegative integer")
        self._temp_dir = temp_dir
        self._spawn, self._kill = spawn, kill
        self._terminal_notification = terminal_notification
        self._max_active, self._max_records = max_active, max_records
        self._stages: dict[str, FileOperationStage] = {}
        self._tombstones: dict[str, _Tombstone] = {}
        self._retire_requested = False

    def operation(self, name: str) -> "BoundFileOperationStages":
        self._definition(name)
        return BoundFileOperationStages(self, name)

    def _definition(self, name: str) -> FileOperationDefinition:
        operation = self._operations.get(name)
        if operation is None:
            raise ValueError(f"File operation {name!r} is not registered")
        return operation

    @staticmethod
    def _same_binding(
        actual: FileOperationBinding, expected: FileOperationBinding, *, label: str
    ) -> None:
        if actual != expected:
            raise ValueError(f"{label} stage identity does not match its existing binding")

    @staticmethod
    def _same_operation(actual: str, expected: str, *, label: str) -> None:
        if actual != expected:
            raise ValueError(f"{label} stage operation does not match its existing operation")

    def prepare(self, operation_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        operation = self._definition(operation_name)
        binding = binding_from_payload(payload)
        input_value = payload.get("input")
        if not isinstance(input_value, Mapping):
            raise ValueError(f"{operation.label} input is required")
        manifest_value = input_value.get("manifest")
        if not isinstance(manifest_value, Mapping):
            raise ValueError(f"{operation.label} input manifest is required")
        manifest = FileTransferManifest.from_dict(
            manifest_value, max_total_size=operation.input_max_bytes
        )
        operation.validate_input(manifest)
        tombstone = self._tombstones.get(binding.stage_id)
        if tombstone is not None:
            self._same_binding(tombstone.binding, binding, label=operation.label)
            self._same_operation(tombstone.operation_name, operation_name, label=operation.label)
            raise ValueError(f"{operation.label} stage has already been released")
        existing = self._stages.get(binding.stage_id)
        if existing is not None:
            self._same_binding(existing.binding, binding, label=operation.label)
            self._same_operation(existing.operation.name, operation_name, label=operation.label)
            if existing.manifest != manifest:
                raise ValueError(
                    f"{operation.label} input manifest conflicts with the prepared stage"
                )
            return self._prepared_response(existing)
        if self._retire_requested:
            raise RuntimeError(
                f"{operation.label} stage journal is full; launcher retirement is requested"
            )
        if len(self._stages) >= self._max_active:
            raise RuntimeError(f"{operation.label} stage limit is reached")
        if len(self._stages) + len(self._tombstones) >= self._max_records:
            raise RuntimeError(f"{operation.label} stage record limit is reached")
        # Reserve staging space before accepting payload bytes. Prepared
        # transfers share this registry, so several large snapshots cannot
        # each spend the same still-free space. Terminal files are already
        # accounted by disk_usage and require no further growth reservation.
        pending_bytes = sum(
            max(0, stage.manifest.total_size - min(
                stage.manifest.total_size,
                stage.receiver._next_index * stage.manifest.chunk_size,
            )) + stage.operation.workspace_max_bytes
            for stage in self._stages.values()
            if stage.state in ("prepared", "running")
        )
        required_bytes = manifest.total_size + operation.workspace_max_bytes
        free_bytes = shutil.disk_usage(self._temp_dir or tempfile.gettempdir()).free
        if free_bytes - pending_bytes - DISK_HEADROOM_BYTES < required_bytes:
            raise FileTransferError(
                f"{operation.label} needs {required_bytes} bytes of unreserved staging space."
            )
        directory = Path(
            tempfile.mkdtemp(
                prefix=f"{operation.temp_prefix}{binding.stage_id}-", dir=self._temp_dir
            )
        )
        try:
            receiver = FileTransferReceiver(manifest, temp_dir=directory)
            stage = FileOperationStage(
                operation=operation,
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

    def add_chunk(self, operation_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        stage = self._stage(operation_name, payload)
        if stage.state != "prepared":
            raise ValueError(f"{stage.operation.label} input can only be written while prepared")
        chunk = payload.get("chunk")
        if not isinstance(chunk, Mapping):
            raise ValueError(f"{stage.operation.label} input chunk is required")
        try:
            stage.receiver.add_chunk(chunk)
        except FileTransferError as exc:
            raise ValueError(str(exc)) from exc
        return {
            **binding_dict(stage.binding),
            "accepted": True,
            "input_next_index": stage.receiver._next_index,
        }

    async def start(self, operation_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        stage = self._stage(operation_name, payload)
        if stage.state != "prepared":
            return self._status(stage)
        try:
            stage.input_transfer = stage.receiver.finish()
        except FileTransferError as exc:
            self._fail(stage, "buildfailure", str(exc))
            await self._notify_terminal(stage)
            return self._status(stage)
        stage.state = "running"
        try:
            instruction = stage.operation.child_instruction(stage)
            child = stage.operation.child()
            stage.process = await self._spawn(*child.args, **child.kwargs)
        except Exception as exc:
            self._fail(
                stage,
                "buildfailure",
                f"{stage.operation.label} child could not start: {exc}",
            )
            await self._notify_terminal(stage)
            return self._status(stage)
        stage.sampler = CPUSampler(
            pid=stage.process.pid or -1, clock_ticks=get_clock_ticks()
        )
        stage.sampler.set_baseline(time.monotonic())
        stage.task = asyncio.create_task(self._wait(stage, instruction))
        return self._status(stage)

    async def _wait(self, stage: FileOperationStage, instruction: dict[str, object]) -> None:
        assert stage.process is not None
        try:
            stdout = await self._child_receipt(stage.process, instruction, stage.operation)
            receipt = json.loads(stdout)
            if not isinstance(receipt, Mapping):
                raise ValueError(f"{stage.operation.label} child returned an invalid receipt")
            if receipt.get("state") == "succeeded" and stage.process.returncode == 0:
                stage.result = stage.operation.decode_success(receipt, stage)
                stage.state = "succeeded"
            else:
                error = receipt.get("error")
                if (
                    not isinstance(error, Mapping)
                    or not isinstance(error.get("category"), str)
                    or not isinstance(error.get("message"), str)
                ):
                    raise ValueError(
                        f"{stage.operation.label} child failed without a structured error"
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

    async def _notify_terminal(self, stage: FileOperationStage) -> None:
        if self._terminal_notification is not None and stage.state in {"succeeded", "failed"}:
            await self._terminal_notification(binding_dict(stage.binding))

    async def _child_receipt(
        self,
        process: _Process,
        instruction: dict[str, object],
        operation: FileOperationDefinition,
    ) -> bytes:
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
                raise ValueError(f"{operation.label} child returned an oversized receipt")
        await process.wait()
        return bytes(receipt)

    def status(self, operation_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        operation = self._definition(operation_name)
        binding = binding_from_payload(payload)
        tombstone = self._tombstones.get(binding.stage_id)
        if tombstone is not None:
            self._same_binding(tombstone.binding, binding, label=operation.label)
            self._same_operation(tombstone.operation_name, operation_name, label=operation.label)
            return {**binding_dict(binding), "state": "released", "released": True}
        stage = self._stages.get(binding.stage_id)
        if stage is None:
            return {**binding_dict(binding), "state": "unknown", "input_next_index": 0}
        self._same_binding(stage.binding, binding, label=operation.label)
        self._same_operation(stage.operation.name, operation_name, label=operation.label)
        return self._status(stage)

    def result_chunk(
        self, operation_name: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        stage = self._stage(operation_name, payload)
        if stage.state != "succeeded" or stage.result is None:
            raise ValueError(f"{stage.operation.label} result is not available")
        index = payload.get("index")
        if type(index) is not int or not 0 <= index < stage.result.manifest.chunk_count:
            raise ValueError(f"{stage.operation.label} result chunk index is invalid")
        with stage.output_path.open("rb") as archive:
            archive.seek(index * stage.result.manifest.chunk_size)
            data = archive.read(stage.result.manifest.chunk_size)
        return {
            **binding_dict(stage.binding),
            "chunk": FileTransferChunk(
                index=index, data_b64=base64.b64encode(data).decode("ascii")
            ).to_dict(),
        }

    async def release(self, operation_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        operation = self._definition(operation_name)
        binding = binding_from_payload(payload)
        tombstone = self._tombstones.get(binding.stage_id)
        if tombstone is not None:
            self._same_binding(tombstone.binding, binding, label=operation.label)
            self._same_operation(tombstone.operation_name, operation_name, label=operation.label)
            return {**binding_dict(binding), "released": True}
        stage = self._stages.get(binding.stage_id)
        if stage is None:
            raise ValueError(f"Unknown {operation.label} stage")
        self._same_binding(stage.binding, binding, label=operation.label)
        self._same_operation(stage.operation.name, operation_name, label=operation.label)
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
        self._stages.pop(binding.stage_id, None)
        self._tombstones[binding.stage_id] = _Tombstone(operation_name, binding)
        if len(self._tombstones) >= self._max_records:
            self._retire_requested = True
        return {**binding_dict(binding), "released": True}

    async def shutdown(self) -> None:
        for stage in list(self._stages.values()):
            with contextlib.suppress(Exception):
                await self.release(stage.operation.name, binding_dict(stage.binding))

    @property
    def has_live(self) -> bool:
        return any(stage.state == "running" for stage in self._stages.values())

    @property
    def has_stages(self) -> bool:
        return bool(self._stages)

    @property
    def retire_requested(self) -> bool:
        return self._retire_requested

    def inventory(self, operation_name: str | None = None) -> list[dict[str, Any]]:
        if operation_name is not None:
            self._definition(operation_name)
        return [
            self._inventory(stage)
            for stage in self._stages.values()
            if operation_name is None or stage.operation.name == operation_name
        ]

    def telemetry(self, operation_name: str | None = None) -> list[dict[str, Any]]:
        if operation_name is not None:
            self._definition(operation_name)
        records = []
        for stage in self._stages.values():
            if operation_name is not None and stage.operation.name != operation_name:
                continue
            sampler = stage.sampler
            if sampler is not None and stage.state == "running":
                sampler.sample(time.monotonic())
            records.append(
                {
                    "stage_id": stage.binding.stage_id,
                    "pid": stage.process.pid if stage.process is not None else None,
                    "state": stage.state,
                    "rss_bytes": sampler.last_process_rss_bytes if sampler else None,
                    "peak_memory_bytes": sampler.peak_process_rss_bytes if sampler else None,
                    "peak_cpu_cores": sampler.peak_cpu_cores if sampler else None,
                }
            )
        return records

    def _stage(
        self, operation_name: str, payload: Mapping[str, Any]
    ) -> FileOperationStage:
        operation = self._definition(operation_name)
        binding = binding_from_payload(payload)
        stage = self._stages.get(binding.stage_id)
        if stage is None:
            raise ValueError(f"Unknown {operation.label} stage")
        self._same_binding(stage.binding, binding, label=operation.label)
        self._same_operation(stage.operation.name, operation_name, label=operation.label)
        return stage

    @staticmethod
    def _prepared_response(stage: FileOperationStage) -> dict[str, Any]:
        return {
            **binding_dict(stage.binding),
            "state": stage.state,
            "accepted": True,
            "input_next_index": stage.receiver._next_index,
        }

    @staticmethod
    def _fail(stage: FileOperationStage, category: str, message: str) -> None:
        stage.state, stage.error = (
            "failed",
            {"category": category, "message": message[:4096]},
        )

    @staticmethod
    def _status(stage: FileOperationStage) -> dict[str, Any]:
        response: dict[str, Any] = {
            **binding_dict(stage.binding),
            "state": stage.state,
        }
        if stage.state == "prepared":
            response["input_next_index"] = stage.receiver._next_index
        if stage.process is not None:
            response["pid"] = stage.process.pid
        if stage.error is not None:
            response["error"] = stage.error
        if stage.result is not None:
            response["result_manifest"] = stage.result.manifest.to_dict()
            response["result_max_total_size"] = stage.result.manifest.max_total_size
            response.update(stage.result.fields)
        return response

    def _inventory(self, stage: FileOperationStage) -> dict[str, Any]:
        record = self._status(stage)
        record.pop("input_next_index", None)
        record["operation"] = stage.operation.name
        record["input_sha256"] = stage.manifest.sha256
        return record


class BoundFileOperationStages:
    """A local-code view of one operation in the shared stage registry."""

    def __init__(self, engine: NamedFileOperationStages, operation_name: str) -> None:
        self._engine, self.operation_name = engine, operation_name

    def prepare(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._engine.prepare(self.operation_name, payload)

    def add_chunk(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._engine.add_chunk(self.operation_name, payload)

    async def start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self._engine.start(self.operation_name, payload)

    def status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._engine.status(self.operation_name, payload)

    def result_chunk(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._engine.result_chunk(self.operation_name, payload)

    async def release(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self._engine.release(self.operation_name, payload)

    @property
    def has_live(self) -> bool:
        return self._engine.has_live

    @property
    def has_stages(self) -> bool:
        return self._engine.has_stages

    @property
    def retire_requested(self) -> bool:
        return self._engine.retire_requested

    def inventory(self) -> list[dict[str, Any]]:
        return self._engine.inventory(self.operation_name)

    def telemetry(self) -> list[dict[str, Any]]:
        return self._engine.telemetry(self.operation_name)
