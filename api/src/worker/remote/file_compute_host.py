"""Fenced API orchestration for code-registered launcher file stages."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import Callable
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import func, select

from shared.app_build_compute import MAX_RESULT_TRANSFER_BYTES as APP_BUILD_RESULT_LIMIT
from shared.app_bundle_compute import MAX_RESULT_TRANSFER_BYTES as APP_BUNDLE_RESULT_LIMIT
from shared.audit_archive_compute import MAX_RESULT_TRANSFER_BYTES as AUDIT_ARCHIVE_RESULT_LIMIT
from shared.audit_export_compute import MAX_RESULT_TRANSFER_BYTES as AUDIT_EXPORT_RESULT_LIMIT
from shared.file_compute import FileComputeResult
from shared.worker_compute import APP_COMPILE_CAPABILITY_NAME, AUDIT_ARCHIVE_CAPABILITY_NAME
from shared.worker_compute import APP_BUNDLE_CAPABILITY_NAME
from shared.worker_compute import AUDIT_EXPORT_CAPABILITY_NAME
from shared.worker_transfer import FileTransfer, FileTransferChunk, FileTransferManifest, FileTransferReceiver
from src.core.database import get_db_context
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.jobs.platform.catalog import get_platform_job_metadata, platform_job_types_for_compute
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.worker.remote.fencing import LeaseRef, fence_remote_owner, remote_owner_context
from src.worker.remote.ownership import DEFAULT_OWNER_LEASE_SECONDS


_OPERATION_CAPABILITIES = {
    "app.compile.v2": APP_COMPILE_CAPABILITY_NAME,
    "app.bundle.v1": APP_BUNDLE_CAPABILITY_NAME,
    "audit.archive.v1": AUDIT_ARCHIVE_CAPABILITY_NAME,
    "audit.export.v1": AUDIT_EXPORT_CAPABILITY_NAME,
}
_OPERATION_RESULT_LIMITS = {
    "app.compile.v2": APP_BUILD_RESULT_LIMIT,
    "app.bundle.v1": APP_BUNDLE_RESULT_LIMIT,
    "audit.archive.v1": AUDIT_ARCHIVE_RESULT_LIMIT,
    "audit.export.v1": AUDIT_EXPORT_RESULT_LIMIT,
}
FILE_COMPUTE_JOB_TYPES = frozenset().union(*(
    platform_job_types_for_compute(capability)
    for capability in _OPERATION_CAPABILITIES.values()
))


def operation_for_capability(capability: str | None) -> str | None:
    return next((operation for operation, value in _OPERATION_CAPABILITIES.items() if value == capability), None)


def operation_for_job_type(job_type: str) -> str | None:
    metadata = get_platform_job_metadata(job_type)
    return operation_for_capability(metadata.compute_capability if metadata else None)


def capability_for_operation(operation_name: str) -> str | None:
    return _OPERATION_CAPABILITIES.get(operation_name)


def _bound_reply(reply: Mapping[str, Any], binding: Mapping[str, str]) -> dict[str, Any]:
    if any(reply.get(key) != value for key, value in binding.items()):
        raise PlatformJobFailure("file_compute_stage_conflict", "File compute reply has a different claim binding.")
    return dict(reply)


@asynccontextmanager
async def control_authorization(bridge: Any, claim: ClaimedPlatformJob | None = None):
    lease = bridge.lease
    with remote_owner_context(LeaseRef(lease)):
        async with get_db_context() as db:
            if not await fence_remote_owner(db):
                raise PlatformJobCancelled
            seconds = await db.scalar(select(func.extract(
                "epoch", ExecutionWorker.remote_owner_lease_expires_at - func.clock_timestamp(),
            )).where(ExecutionWorker.id == lease.worker_id))
            if not seconds or float(seconds) <= 0:
                raise PlatformJobCancelled
            async with asyncio.timeout(float(seconds)):
                if claim is not None and not await db.scalar(select(PlatformJob.id).where(
                    PlatformJob.id == claim.id, PlatformJob.lease_token == claim.lease_token,
                    PlatformJob.status == "running",
                ).with_for_update()):
                    raise PlatformJobCancelled
                yield


class RemoteFileCompute:
    """One file operation attached to an existing claimed job, never a new job."""

    def __init__(self, bridge: Any, claim: ClaimedPlatformJob, operation_name: str):
        capability = _OPERATION_CAPABILITIES.get(operation_name)
        if capability is None:
            raise ValueError("File compute operation is not code-registered")
        self.bridge, self.claim = bridge, claim
        self.operation_name, self.capability = operation_name, capability
        self.invoked = self.release_requested = False
        self.predecessor_stage_id: UUID | None = None
        self.result_metadata_validator: Callable[[Mapping[str, Any]], None] | None = None
        self._new_stage()

    def _new_stage(self) -> None:
        self.stage_id = str(uuid4())
        self.binding = {
            "stage_id": self.stage_id,
            "job_id": str(self.claim.id),
            "lease_token": str(self.claim.lease_token),
        }
        self.input_sha256 = None
        self._stages()[self.stage_id] = self

    def _stages(self) -> dict[str, Any]:
        return self.bridge.file_computes

    def _action(self, action: str) -> str:
        return f"app_compile_{action}" if self.operation_name == "app.compile.v2" else f"file_compute_{action}"

    def _payload(self, **payload: Any) -> dict[str, Any]:
        value = {**self.binding, **payload}
        if self.operation_name != "app.compile.v2":
            value["operation"] = self.operation_name
        return value

    @asynccontextmanager
    async def authorize(self, *, release: bool = False):
        async with control_authorization(self.bridge, None if release else self.claim):
            yield

    async def _ready(self, *, cleanup: bool = False) -> None:
        async with asyncio.timeout(DEFAULT_OWNER_LEASE_SECONDS if cleanup else self.claim.timeout_seconds):
            while True:
                self.bridge.transport_changed.clear()
                if self.bridge.connected and self.bridge.reconciliation_ready.is_set():
                    return
                await self.bridge.transport_changed.wait()

    async def _read(self, action: str, **payload: Any) -> dict[str, Any]:
        while True:
            await self._ready()
            try:
                return _bound_reply(await self.bridge.request(self._action(action), self._payload(**payload), authorization=self.authorize()), self.binding)
            except (ConnectionError, TimeoutError):
                await asyncio.sleep(.1)

    async def _mutate(self, action: str, **payload: Any) -> bool:
        await self._ready()
        try:
            await self.bridge.request(self._action(action), self._payload(**payload), authorization=self.authorize())
            return True
        except (ConnectionError, TimeoutError):
            return False

    async def _checkpoint(self, manifest: FileTransferManifest) -> None:
        from src.services.platform_jobs import PlatformJobExecutorAttributionConflict, record_platform_job_executor_attribution

        with remote_owner_context(LeaseRef(self.bridge.lease)):
            try:
                recorded = await record_platform_job_executor_attribution(
                    self.claim.id, self.claim.lease_token, worker_id=self.bridge.lease.worker_id,
                    compute_capability=self.capability, stage_id=UUID(self.stage_id),
                    input_sha256=manifest.sha256, phase="Running File Compute",
                    predecessor_stage_id=self.predecessor_stage_id,
                )
            except PlatformJobExecutorAttributionConflict as exc:
                raise PlatformJobFailure("file_compute_stage_exists", "This attempt already prepared a file compute stage.") from exc
        if not recorded:
            raise PlatformJobCancelled
        self.input_sha256 = manifest.sha256

    async def __call__(self, operation_name: str, transfer: FileTransfer) -> FileComputeResult:
        if operation_name != self.operation_name:
            raise PlatformJobFailure("file_compute_operation_mismatch", "This job cannot request a different file compute operation.")
        if self.invoked:
            await self._release_current()
            self._new_stage()
        self.invoked = True
        await self._checkpoint(transfer.manifest)
        status = await self._read("status")
        while status["state"] == "unknown":
            await self._mutate("prepare", input={"manifest": transfer.manifest.to_dict()})
            status = await self._read("status")
        if status["state"] != "prepared":
            raise PlatformJobFailure("file_compute_stage_conflict", "File compute was already started for this stage.")
        for chunk in transfer.iter_chunks():
            if chunk.index < status["input_next_index"]:
                continue
            while status["input_next_index"] == chunk.index:
                await self._mutate("chunk", chunk=chunk.to_dict())
                status = await self._read("status")
            if status["input_next_index"] != chunk.index + 1:
                raise PlatformJobFailure("file_compute_transfer_invalid", "File compute input offset changed unexpectedly.")
        await self._mutate("start")
        while True:
            self.bridge.compute_changed.clear()
            status = await self._read("status")
            if status["state"] == "prepared":
                await self._mutate("start")
                continue
            if status["state"] != "running":
                break
            await self.bridge.compute_changed.wait()
        if status["state"] != "succeeded":
            error = status.get("error", {})
            message = error.get("message", "Remote file compute failed.") if isinstance(error, Mapping) else "Remote file compute failed."
            category = error.get("category", "failed") if isinstance(error, Mapping) else "failed"
            code = {"memory": "memory_pressure", "timeout": "timeout"}.get(
                category, f"file_compute_{category}"
            )
            raise PlatformJobFailure(code, str(message)[:4000])
        raw_manifest = status.get("result_manifest")
        result_max_total_size = status.get("result_max_total_size")
        expected_limit = _OPERATION_RESULT_LIMITS[self.operation_name]
        if (
            not isinstance(raw_manifest, Mapping)
            or result_max_total_size != expected_limit
        ):
            raise PlatformJobFailure("file_compute_transfer_invalid", "The physical worker returned an invalid result manifest.")
        manifest = FileTransferManifest.from_dict(raw_manifest, max_total_size=expected_limit)
        if self.result_metadata_validator is not None:
            self.result_metadata_validator(status)
        with FileTransferReceiver(manifest) as receiver:
            for index in range(manifest.chunk_count):
                reply = await self._read("result_chunk", index=index)
                receiver.add_chunk(FileTransferChunk.from_dict(reply["chunk"]))
            received = receiver.finish()
        metadata = {key: value for key, value in status.items() if key not in {
            "stage_id", "job_id", "lease_token", "state", "pid", "result_manifest",
            "result_max_total_size", "error", "input_next_index",
        }}
        return FileComputeResult(transfer=received, metadata=metadata)

    async def release(self) -> None:
        self.release_requested = True
        if self.invoked:
            await self._release_current()
        self._stages().pop(self.stage_id, None)

    async def _release_current(self) -> None:
        old_stage_id = UUID(self.stage_id)
        await self._ready(cleanup=True)
        async with asyncio.timeout(DEFAULT_OWNER_LEASE_SECONDS):
            while True:
                status = _bound_reply(await self.bridge.request(
                    self._action("status"), self._payload(),
                    authorization=control_authorization(self.bridge),
                ), self.binding)
                if status["state"] in ("unknown", "released"):
                    break
                try:
                    await self.bridge.request(
                        self._action("release"), self._payload(),
                        authorization=control_authorization(self.bridge),
                    )
                except TimeoutError:
                    continue
        self.predecessor_stage_id = old_stage_id
        self._stages().pop(self.stage_id, None)


async def reconcile_file_computes(bridge: Any) -> None:
    """Stop orphan compute before durable runner-loss recovery releases memory."""
    from src.jobs.schedulers.platform_jobs import _handle_runner_loss

    stages = (bridge.inventory or {}).get("compute_stages", [])
    if not isinstance(stages, list) or len(stages) > 64:
        raise ValueError("Invalid file compute inventory")
    local_stages = bridge.file_computes
    for record in stages:
        if not isinstance(record, Mapping):
            raise ValueError("Invalid file compute inventory record")
        operation = record.get("operation", "app.compile.v2")
        capability = _OPERATION_CAPABILITIES.get(operation)
        if capability is None:
            raise ValueError("File compute inventory has an unregistered operation")
        binding = {key: str(UUID(str(record[key]))) for key in ("stage_id", "job_id", "lease_token")}
        digest = record.get("input_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("File compute inventory is missing its input digest")
        local = local_stages.get(binding["stage_id"])
        if local is not None and not local.release_requested:
            if local.binding != binding or local.input_sha256 != digest or local.operation_name != operation:
                raise ValueError("File compute inventory changed its claim binding")
            continue
        bound = False
        if local is None:
            async with get_db_context() as db:
                job = await db.scalar(select(PlatformJob).where(
                    PlatformJob.id == UUID(binding["job_id"]), PlatformJob.lease_token == UUID(binding["lease_token"]),
                    PlatformJob.status == "running", PlatformJob.lease_expires_at > func.now(),
                ))
                bound = bool(job is not None and job.executor_attempt == job.attempt
                    and job.executor_worker_id == bridge.lease.worker_id
                    and str(job.executor_stage_id) == binding["stage_id"]
                    and job.executor_input_sha256 == digest
                    and job.executor_compute_capability == capability)
        orphan = RemoteFileCompute.__new__(RemoteFileCompute)
        orphan.bridge, orphan.binding = bridge, binding
        orphan.operation_name, orphan.capability = operation, capability
        orphan.invoked = orphan.release_requested = True
        orphan.stage_id, orphan.input_sha256, orphan.claim = binding["stage_id"], digest, None
        await orphan.release()
        if bound:
            await _handle_runner_loss(
                UUID(binding["job_id"]), UUID(binding["lease_token"]), error_code="runner_lost",
                error_message="The API file-compute orchestrator stopped before the operation completed.",
                memory_sample=bridge.memory(),
            )
        local_stages.pop(binding["stage_id"], None)


class ApiFileComputeProcess:
    """Canonical handler process with physical calculation as a scoped dependency."""

    def __init__(self, bridge: Any, claim: ClaimedPlatformJob, operation_name: str, app_build_compute: Any = None):
        self.compute = RemoteFileCompute(bridge, claim, operation_name)
        self.app_build_compute = app_build_compute(self.compute) if app_build_compute is not None else None
        self.bridge, self.returncode = bridge, None
        self.task = asyncio.create_task(self._run(claim), name=f"file-compute:{claim.id}")

    async def _run(self, claim: ClaimedPlatformJob) -> int:
        from src.jobs.platform.runner import run_claimed_platform_job

        code = 1
        try:
            ref = self.bridge.lease_ref
            if ref is None:
                raise RuntimeError("File compute requires an initialized remote session")
            with remote_owner_context(ref):
                complete = await run_claimed_platform_job(
                    claim.id, claim.lease_token, file_compute=self.compute,
                    app_build_compute=self.app_build_compute,
                )
            code = 0 if complete else 1
            return code
        finally:
            await self._cleanup_until_settled()
            self.returncode = code

    async def wait(self) -> int:
        return await asyncio.shield(self.task)

    async def terminate(self) -> None:
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def _cleanup_until_settled(self) -> None:
        """Retain admission until stage release is observed or ownership is lost."""
        from src.worker.remote.ownership import owns_remote_session

        while True:
            try:
                await self.compute.release()
                return
            except PlatformJobCancelled:
                self.compute._stages().pop(self.compute.stage_id, None)
                return
            except (ConnectionError, TimeoutError):
                try:
                    still_owner = await owns_remote_session(self.bridge.lease)
                except Exception:
                    still_owner = True
                if not still_owner:
                    self.compute._stages().pop(self.compute.stage_id, None)
                    return
                self.bridge.transport_changed.clear()
                try:
                    await asyncio.wait_for(self.bridge.transport_changed.wait(), 1)
                except TimeoutError:
                    pass


class ApiFileComputeBackend:
    def __init__(self, bridge: Any, app_build_compute: Any = None):
        self.bridge = bridge
        self.app_build_compute = app_build_compute

    async def spawn(self, claim: ClaimedPlatformJob) -> ApiFileComputeProcess:
        operation = operation_for_job_type(claim.job_type)
        if operation is None:
            raise PlatformJobFailure("unsupported_compute_job", "This worker does not support the requested platform job.")
        return ApiFileComputeProcess(
            self.bridge, claim, operation,
            self.app_build_compute if operation == "app.compile.v2" else None,
        )

    def memory(self) -> tuple[int, int]:
        return self.bridge.memory()
