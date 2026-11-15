"""Fenced App compilation over the existing physical-worker control channel."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from bifrost.app_build_identity import sdk_identity, valid_recipe
from shared.app_build_compute import (
    MAX_RESULT_TRANSFER_BYTES,
    decode_app_build_result,
    encode_app_build_inputs,
)
from shared.solution_app_compiler import AppBuildInputs, AppBuildResult, source_build_identity
from shared.worker_compute import APP_COMPILE_CAPABILITY_NAME
from shared.worker_transfer import FileTransferChunk, FileTransferManifest, FileTransferReceiver
from src.core.database import get_db_context
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.jobs.platform.catalog import platform_job_types_for_compute
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.worker.remote.fencing import LeaseRef, fence_remote_owner, remote_owner_context
from src.worker.remote.ownership import DEFAULT_OWNER_LEASE_SECONDS


APP_COMPILE_JOB_TYPES = platform_job_types_for_compute(APP_COMPILE_CAPABILITY_NAME)
logger = logging.getLogger(__name__)


def _bound_reply(reply: dict[str, Any], binding: dict[str, str]) -> dict[str, Any]:
    if any(reply.get(key) != value for key, value in binding.items()):
        raise PlatformJobFailure("compile_stage_conflict", "App compilation reply has a different claim binding.")
    return reply


@asynccontextmanager
async def control_authorization(bridge: Any, claim: ClaimedPlatformJob | None = None):
    """Fence the small command send, releasing locks before its reply."""
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


async def _release_stage(bridge: Any, binding: dict[str, str]) -> None:
    async with asyncio.timeout(DEFAULT_OWNER_LEASE_SECONDS):
        while True:
            status = _bound_reply(await bridge.request("app_compile_status", binding,
                                          authorization=control_authorization(bridge)), binding)
            if status["state"] in ("unknown", "released"):
                return
            try:
                await bridge.request("app_compile_release", binding,
                                     authorization=control_authorization(bridge))
            except TimeoutError:
                # DELETE is still uncertain. Read its retained tombstone
                # before deciding whether cleanup needs anything else.
                continue


async def reconcile_app_compiles(bridge: Any) -> None:
    """Resolve orphan compute before this worker may accept another claim.

    An API process loss does not replay an App deployment handler. Its existing
    max-attempt/runner-loss policy decides the durable outcome; physical compute
    from that lost orchestration is stopped before reclaiming its memory.
    """
    from src.jobs.schedulers.platform_jobs import _handle_runner_loss

    inventory = bridge.inventory or {}
    stages = inventory.get("compute_stages", [])
    if not isinstance(stages, list) or len(stages) > 64:
        raise ValueError("Invalid App compilation inventory")
    for stage in stages:
        binding = {key: str(UUID(stage[key])) for key in ("stage_id", "job_id", "lease_token")}
        digest = stage.get("input_sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("App compilation inventory is missing its input digest")
        local = bridge.app_compilers.get(binding["stage_id"])
        if local is not None and not local.release_requested:
            if local.binding != binding or local.input_sha256 != digest:
                raise ValueError("App compilation inventory changed its claim binding")
            continue
        bound = False
        if local is None:
            async with get_db_context() as db:
                job = await db.scalar(select(PlatformJob).where(
                    PlatformJob.id == UUID(binding["job_id"]),
                    PlatformJob.lease_token == UUID(binding["lease_token"]),
                    PlatformJob.status == "running",
                    PlatformJob.lease_expires_at > func.now(),
                ))
                bound = (
                    job is not None
                    and job.executor_attempt == job.attempt
                    and job.executor_worker_id == bridge.lease.worker_id
                    and str(job.executor_stage_id) == binding["stage_id"]
                    and job.executor_input_sha256 == digest
                    and job.executor_compute_capability == APP_COMPILE_CAPABILITY_NAME
                )
        await _release_stage(bridge, binding)
        if bound:
            await _handle_runner_loss(
                UUID(binding["job_id"]), UUID(binding["lease_token"]),
                error_code="runner_lost",
                error_message="The API build orchestrator stopped before the deployment completed.",
                memory_sample=bridge.memory(),
            )
        bridge.app_compilers.pop(binding["stage_id"], None)


class RemoteAppCompiler:
    """One compute stage of an existing claimed PlatformJob, never another job.

    Mutations are resolved from stage readback after an uncertain reply. The
    worker retains the output until the API's job handler has finished its
    fenced activation and this owner explicitly releases the stage.
    """

    def __init__(self, bridge: Any, claim: ClaimedPlatformJob):
        self.bridge, self.claim = bridge, claim
        self.stage_id = str(uuid4())
        self.binding = {"stage_id": self.stage_id, "job_id": str(claim.id),
                        "lease_token": str(claim.lease_token)}
        self.input_sha256: str | None = None
        self.invoked = False
        self.release_requested = False
        bridge.app_compilers[self.stage_id] = self

    @asynccontextmanager
    async def authorize(self, *, release: bool = False):
        """Hold worker/job fences through sending, never while awaiting a reply."""
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
                return _bound_reply(await self.bridge.request(
                    action, {**self.binding, **payload}, authorization=self.authorize(),
                ), self.binding)
            except (ConnectionError, TimeoutError):
                # This is a read. Mutations below always read back before
                # deciding whether any remaining bytes/launch are required.
                await asyncio.sleep(0.1)

    async def _mutate(self, action: str, **payload: Any) -> bool:
        await self._ready()
        try:
            await self.bridge.request(
                action, {**self.binding, **payload}, authorization=self.authorize(),
            )
            return True
        except (ConnectionError, TimeoutError):
            return False

    async def _checkpoint(self, manifest: FileTransferManifest) -> None:
        from src.services.platform_jobs import (
            PlatformJobExecutorAttributionConflict,
            record_platform_job_executor_attribution,
        )

        with remote_owner_context(LeaseRef(self.bridge.lease)):
            try:
                recorded = await record_platform_job_executor_attribution(
                    self.claim.id,
                    self.claim.lease_token,
                    worker_id=self.bridge.lease.worker_id,
                    compute_capability=APP_COMPILE_CAPABILITY_NAME,
                    stage_id=UUID(self.stage_id),
                    input_sha256=manifest.sha256,
                    phase="Compiling App",
                )
            except PlatformJobExecutorAttributionConflict as exc:
                raise PlatformJobFailure(
                    "compile_stage_exists",
                    "This attempt already prepared an App compilation stage.",
                ) from exc
        if not recorded:
            raise PlatformJobCancelled
        self.input_sha256 = manifest.sha256

    async def __call__(self, inputs: AppBuildInputs) -> AppBuildResult:
        if self.invoked:
            raise PlatformJobFailure("compile_stage_exists", "An App build attempt may compile only once.")
        self.invoked = True
        transfer = await asyncio.to_thread(encode_app_build_inputs, inputs)
        try:
            await self._checkpoint(transfer.manifest)
            status = await self._read("app_compile_status")
            while status["state"] == "unknown":
                await self._mutate("app_compile_prepare", input={"manifest": transfer.manifest.to_dict()})
                status = await self._read("app_compile_status")
            if status["state"] != "prepared":
                raise PlatformJobFailure("compile_stage_conflict", "App compilation was already started for this stage.")
            for chunk in transfer.iter_chunks():
                if chunk.index < status["input_next_index"]:
                    continue
                while status["input_next_index"] == chunk.index:
                    await self._mutate("app_compile_chunk", chunk=chunk.to_dict())
                    status = await self._read("app_compile_status")
                if status["input_next_index"] != chunk.index + 1:
                    raise PlatformJobFailure("compile_transfer_invalid", "App compilation input offset changed unexpectedly.")
            await self._mutate("app_compile_start")
            while True:
                self.bridge.compute_changed.clear()
                status = await self._read("app_compile_status")
                if status["state"] == "prepared":
                    # A lost start reply is resolved by this readback. The
                    # same immutable stage cannot launch a second process.
                    await self._mutate("app_compile_start")
                    continue
                if status["state"] != "running":
                    break
                await self.bridge.compute_changed.wait()
            if status["state"] != "succeeded":
                error = status.get("error", {})
                category = error.get("category", "buildfailure")
                code = {"memory": "memory_pressure", "timeout": "timeout",
                        "lockfile": "sdk_update_lockfile_unreproducible"}.get(category, "app_build_failed")
                raise PlatformJobFailure(code, str(error.get("message", "Remote App compilation failed."))[:4000])
            recipe = status.get("build_recipe")
            if (not valid_recipe(recipe) or recipe["sdk"] != sdk_identity(inputs.sdk_tarball)
                    or recipe["variant"] != inputs.recipe["variant"]):
                raise PlatformJobFailure("compile_provenance_invalid", "The physical compiler returned incompatible build provenance.")
            manifest = FileTransferManifest.from_dict(
                status["result_manifest"], max_total_size=MAX_RESULT_TRANSFER_BYTES
            )
            if manifest.kind != "app_build_result":
                raise PlatformJobFailure("compile_transfer_invalid", "The physical compiler returned the wrong transfer kind.")
            with FileTransferReceiver(manifest) as receiver:
                for index in range(manifest.chunk_count):
                    reply = await self._read("app_compile_result_chunk", index=index)
                    receiver.add_chunk(FileTransferChunk.from_dict(reply["chunk"]))
                with receiver.finish() as received:
                    result = await asyncio.to_thread(decode_app_build_result, received)
            expected = source_build_identity(replace(inputs, recipe=recipe))
            if result.build_identity != expected:
                raise PlatformJobFailure("compile_provenance_invalid", "App artifacts do not match the physical compiler inputs.")
            return result
        finally:
            transfer.close()

    async def release(self) -> None:
        """Stop only this stage; current worker ownership authorizes cleanup."""
        self.release_requested = True
        if self.invoked:
            await self._ready(cleanup=True)
            await _release_stage(self.bridge, self.binding)
        self.bridge.app_compilers.pop(self.stage_id, None)


class ApiAppBuildProcess:
    """API-owned job orchestration with compilation on its admitted worker."""

    def __init__(self, bridge: Any, claim: ClaimedPlatformJob):
        self.compiler = RemoteAppCompiler(bridge, claim)
        self.returncode: int | None = None
        self.task = asyncio.create_task(self._run(claim), name=f"app-build:{claim.id}")

    async def _run(self, claim: ClaimedPlatformJob) -> int:
        from src.jobs.platform.runner import run_claimed_platform_job

        code = 1
        try:
            completed = await run_claimed_platform_job(claim.id, claim.lease_token, app_build_compute=self.compiler)
            code = 0 if completed else 1
            return code
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("API App build orchestration failed", extra={"job_id": str(claim.id)})
            return 1
        finally:
            try:
                await self.compiler.release()
            except (ConnectionError, TimeoutError):
                logger.warning("App compiler cleanup awaits worker reconnect", extra={"job_id": str(claim.id)})
            except PlatformJobCancelled:
                # A new API owner cleans the inventoried stage before claims
                # open. The fenced old owner must not issue another command.
                self.compiler.bridge.app_compilers.pop(self.compiler.stage_id, None)
            finally:
                self.returncode = code

    async def wait(self) -> int:
        return await asyncio.shield(self.task)

    async def terminate(self) -> None:
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        try:
            await self.compiler.release()
        except (ConnectionError, TimeoutError):
            # Inventory must confirm physical cleanup before this worker's
            # admission reopens; the compiler stays in bridge.app_compilers.
            pass
        except PlatformJobCancelled:
            self.compiler.bridge.app_compilers.pop(self.compiler.stage_id, None)


class ApiAppBuildBackend:
    def __init__(self, bridge: Any):
        self.bridge = bridge

    async def spawn(self, claim: ClaimedPlatformJob) -> ApiAppBuildProcess:
        if claim.job_type not in APP_COMPILE_JOB_TYPES:
            raise PlatformJobFailure("unsupported_compute_job", "This worker does not support the requested platform job.")
        return ApiAppBuildProcess(self.bridge, claim)

    def memory(self) -> tuple[int, int]:
        return self.bridge.memory()
