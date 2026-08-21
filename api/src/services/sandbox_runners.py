"""Provider-neutral dispatch for externally sandboxed PlatformJobs."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select

from src.core.database import get_db_context
from src.models.orm.platform_jobs import PlatformJob
from src.services.builder.capabilities import mint_sandbox_job_capability
from src.services.sandbox_runner_config import SandboxRunnerConfigService

_CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
_DISPATCH_TIMEOUT_SECONDS = 20.0
logger = logging.getLogger(__name__)


class SandboxRunnerUnavailable(RuntimeError):
    """The selected provider is not configured and enabled for dispatch."""


class SandboxDispatchFailed(RuntimeError):
    """The selected provider did not accept a sandbox job."""


@dataclass(frozen=True)
class SandboxJobEnvelope:
    """Versioned provider-independent payload received by the runner harness."""

    schema_version: int
    job_id: str
    job_type: str
    dispatch_attempt: int
    callback_base_url: str
    capability: str
    input_sha256: str
    timeout_seconds: int
    runner_sandbox_id: str | None = None
    workspace_sandbox_id: str | None = None
    workspace_broker_url: str | None = None
    runner_allowed_hosts: list[str] = field(default_factory=list)
    workspace_allowed_hosts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SandboxDispatchResult:
    """External identity persisted on the waiting PlatformJob."""

    provider: str
    external_run_id: str
    started_at: datetime
    cancelled: bool = False


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SandboxRunnerUnavailable(f"Sandbox runner {field} is not configured")
    return value.strip()


def _external_instance_id(job: PlatformJob) -> str:
    """Return a stable idempotency key for one fenced dispatch attempt."""

    return f"{job.id}-{job.attempt}"


def _sandbox_base_id(job: PlatformJob) -> str:
    return f"bifrost-{job.id}-{job.attempt}"


def _runner_sandbox_id(job: PlatformJob) -> str:
    return f"{_sandbox_base_id(job)}-runner"


def _workspace_sandbox_id(job: PlatformJob) -> str:
    return f"{_sandbox_base_id(job)}-workspace"


async def dispatch_sandbox_platform_job(
    job_id: UUID,
    lease_token: UUID,
    *,
    input_sha256: str,
) -> SandboxDispatchResult:
    """Dispatch the currently leased attempt through its configured provider."""

    async with get_db_context() as db:
        job = (
            await db.execute(
                select(PlatformJob).where(
                    PlatformJob.id == job_id,
                    PlatformJob.lease_token == lease_token,
                    PlatformJob.status == "running",
                )
            )
        ).scalar_one_or_none()
        if job is None:
            raise SandboxDispatchFailed("Platform job lease is no longer current")

        config = await SandboxRunnerConfigService(db).get_decrypted_internal_config()
        if config is None or not config.get("enabled"):
            raise SandboxRunnerUnavailable("Sandbox runner is not enabled")
        if not config.get("provisioned") or not config.get("connected"):
            raise SandboxRunnerUnavailable("Sandbox runner has not passed readiness checks")

        provider = config.get("provider")
        instance_id = _external_instance_id(job)
        dispatch_attempt = job.attempt

        envelope = None
        if provider == "cloudflare":
            callback_base_url = _required_string(
                config.get("callback_base_url"), "callback address"
            ).rstrip("/")
            envelope = SandboxJobEnvelope(
                schema_version=1,
                job_id=str(job.id),
                job_type=job.job_type,
                dispatch_attempt=dispatch_attempt,
                callback_base_url=callback_base_url,
                capability=mint_sandbox_job_capability(job),
                input_sha256=input_sha256,
                timeout_seconds=job.timeout_seconds,
                runner_sandbox_id=(
                    _runner_sandbox_id(job)
                    if job.job_type == "solution.builder.turn"
                    else None
                ),
                workspace_sandbox_id=(
                    _workspace_sandbox_id(job)
                    if job.job_type == "solution.builder.turn"
                    else None
                ),
                workspace_broker_url=(
                    "http://workspace.bifrost.internal"
                    if job.job_type == "solution.builder.turn"
                    else None
                ),
            )

    if provider == "cloudflare":
        assert envelope is not None
        external_run_id = await _dispatch_cloudflare(
            config,
            envelope,
            instance_id=instance_id,
        )
    elif provider == "local":
        external_run_id = await _dispatch_local(
            job.id,
            dispatch_attempt,
            instance_id=instance_id,
            input_sha256=input_sha256,
        )
    else:
        raise SandboxRunnerUnavailable(f"Unsupported sandbox runner provider: {provider}")

    started_at = datetime.now(timezone.utc)
    cancelled = False
    async with get_db_context() as db:
        current = (
            await db.execute(
                select(PlatformJob)
                .where(
                    PlatformJob.id == job.id,
                    PlatformJob.attempt == job.attempt,
                    PlatformJob.lease_token == lease_token,
                    PlatformJob.status.in_(("running", "cancel_requested")),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if current is None:
            terminal_status = await db.scalar(
                select(PlatformJob.status).where(PlatformJob.id == job.id)
            )
            # A very fast Worker can finish before the scheduler-side handler
            # records its deferred state. That is a successful fenced race,
            # not a cancellation signal.
            cancelled = terminal_status != "succeeded"
        else:
            current.external_provider = str(provider)[:50]
            current.external_run_id = external_run_id[:255]
            current.external_started_at = started_at
            cancelled = current.status == "cancel_requested"
            await db.commit()

    if cancelled:
        await _cancel_dispatched_run(config, str(provider), external_run_id, job.id)

    return SandboxDispatchResult(
        provider=str(provider),
        external_run_id=external_run_id,
        started_at=started_at,
        cancelled=cancelled,
    )


async def _cancel_dispatched_run(
    config: dict[str, Any],
    provider: str,
    run_id: str,
    job_id: UUID,
) -> None:
    """Coordinate a run accepted during a concurrent cancellation race."""
    try:
        if provider == "cloudflare":
            await _cancel_cloudflare(config, run_id)
        elif provider == "local":
            await _cancel_local(config, run_id)
    except (SandboxRunnerUnavailable, SandboxDispatchFailed):
        logger.warning(
            "Sandbox run accepted after cancellation could not be notified",
            extra={"platform_job_id": str(job_id), "external_run_id": run_id},
            exc_info=True,
        )


async def _dispatch_cloudflare(
    config: dict[str, Any],
    envelope: SandboxJobEnvelope,
    *,
    instance_id: str,
) -> str:
    cloudflare = config.get("cloudflare")
    if not isinstance(cloudflare, dict):
        raise SandboxRunnerUnavailable("Cloudflare runner settings are missing")
    account_id = _required_string(cloudflare.get("account_id"), "Cloudflare account")
    workflow_name = _required_string(
        cloudflare.get("workflow_name"), "Cloudflare workflow"
    )
    api_token = _required_string(cloudflare.get("api_token"), "Cloudflare API token")
    url = (
        f"{_CLOUDFLARE_API_BASE}/accounts/{account_id}/workflows/"
        f"{workflow_name}/instances"
    )
    try:
        async with httpx.AsyncClient(timeout=_DISPATCH_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_token}"},
                json={
                    "instance_id": instance_id,
                    "params": asdict(envelope),
                },
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SandboxDispatchFailed("Cloudflare did not accept the sandbox job") from exc

    if not isinstance(body, dict):
        raise SandboxDispatchFailed("Cloudflare returned an invalid workflow response")
    result = body.get("result")
    run_id = result.get("id") if isinstance(result, dict) else None
    if body.get("success") is not True or not isinstance(run_id, str) or not run_id:
        raise SandboxDispatchFailed("Cloudflare returned an invalid workflow response")
    return run_id


async def _dispatch_local(
    job_id: UUID,
    dispatch_attempt: int,
    *,
    instance_id: str,
    input_sha256: str,
) -> str:
    """Publish a reference to the existing Bifrost Worker queue."""
    from src.jobs.rabbitmq import publish_message

    async with get_db_context() as db:
        job_type = await db.scalar(
            select(PlatformJob.job_type).where(PlatformJob.id == job_id)
        )
    queue_name = {
        "solution.build": "solution-builds",
        "solution.builder.turn": "solution-builder-turns",
    }.get(job_type)
    if queue_name is None:
        raise SandboxDispatchFailed("The local Worker does not support this job type")

    try:
        await publish_message(
            queue_name,
            {
                "job_id": str(job_id),
                "dispatch_attempt": dispatch_attempt,
                "input_sha256": input_sha256,
            },
            priority=9,
        )
    except Exception as exc:
        raise SandboxDispatchFailed("The Bifrost Worker did not accept the Builder job") from exc
    return instance_id


async def cancel_external_sandbox_run(job: PlatformJob) -> bool:
    """Best-effort cancellation coordination for a dispatched external run."""
    if not job.external_provider or not job.external_run_id:
        return False
    async with get_db_context() as db:
        config = await SandboxRunnerConfigService(db).get_decrypted_internal_config()
    if config is None or config.get("provider") != job.external_provider:
        logger.warning(
            "Cannot cancel sandbox run because its provider is no longer configured",
            extra={"platform_job_id": str(job.id)},
        )
        return False
    try:
        if job.external_provider == "cloudflare":
            await _cancel_cloudflare(config, job.external_run_id)
        elif job.external_provider == "local":
            await _cancel_local(config, job.external_run_id)
        else:
            return False
    except (SandboxRunnerUnavailable, SandboxDispatchFailed):
        logger.warning(
            "External sandbox run cancellation failed",
            extra={"platform_job_id": str(job.id)},
            exc_info=True,
        )
        return False
    return True


async def _cancel_cloudflare(config: dict[str, Any], run_id: str) -> None:
    """Let the job-bound runner observe durable cancellation and clean up.

    Cloudflare Workflow termination stops orchestration immediately but does
    not terminate a background Sandbox process. The runner polls Bifrost's
    cancellation state every second, reports the fenced terminal result, and
    lets the Workflow destroy its keep-alive sandbox without orphaning cost.
    """
    cloudflare = config.get("cloudflare")
    if not isinstance(cloudflare, dict):
        raise SandboxRunnerUnavailable("Cloudflare runner settings are missing")
    _required_string(run_id, "Cloudflare workflow run")


async def _cancel_local(config: dict[str, Any], run_id: str) -> None:
    """Local workers observe the durable PlatformJob cancellation state."""
    del config, run_id


__all__ = [
    "SandboxDispatchFailed",
    "SandboxDispatchResult",
    "SandboxJobEnvelope",
    "SandboxRunnerUnavailable",
    "cancel_external_sandbox_run",
    "dispatch_sandbox_platform_job",
]
