"""Provision and prove the configured external sandbox runner."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from shared.version import get_version
from src.config import get_settings
from src.core.database import get_db_context
from src.jobs.platform.base import PlatformJobContext, PlatformJobFailure
from src.services.sandbox_runner_config import SandboxRunnerConfigService

_CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
_CLOUDFLARE_PROJECT = Path(__file__).with_name("cloudflare_runner")
_WRANGLER = _CLOUDFLARE_PROJECT / "node_modules" / "wrangler" / "bin" / "wrangler.js"
_WRANGLER_NODE = Path("/usr/local/bin/node22")
_CRANE = Path("/usr/local/bin/crane")
_WORKER_SOURCE = _CLOUDFLARE_PROJECT / "worker.mjs"
_CLOUDFLARE_REGISTRY = "registry.cloudflare.com"
_PROBE_TIMEOUT_SECONDS = 15 * 60
_ROLLOUT_PROBE_BACKOFF_SECONDS = (30.0, 60.0, 90.0)
_OUTPUT_LIMIT = 32_000
logger = logging.getLogger(__name__)


class _CloudflareRolloutNotReady(Exception):
    """Cloudflare accepted the deployment but has not made it runnable yet."""


def configured_runner_image() -> str:
    settings = get_settings()
    tag = settings.builder_runner_image_tag or get_version().removeprefix("v")
    if tag in {"", "unknown", "debug"}:
        tag = "dev"
    tag = re.sub(r"[^A-Za-z0-9_.-]", "-", tag)[:128]
    return f"{settings.builder_runner_image_repository}:{tag}"


def cloudflare_runner_image(account_id: str) -> str:
    """Return the private image reference managed inside one hoster account."""

    tag = configured_runner_image().rsplit(":", 1)[-1]
    return f"{_CLOUDFLARE_REGISTRY}/{account_id}/bifrost-build:{tag}"


async def provision_configured_runner(context: PlatformJobContext) -> dict[str, object]:
    """Provision the selected provider and prove a real runner can start."""
    async with get_db_context() as db:
        service = SandboxRunnerConfigService(db)
        config = await service.get_decrypted_internal_config()
    if config is None:
        raise PlatformJobFailure(
            "sandbox_runner_not_configured",
            "Save a sandbox runner provider before provisioning it.",
        )

    provider = config.get("provider")
    try:
        if provider == "cloudflare":
            details = await _provision_cloudflare(context, config)
        elif provider == "local":
            details = await _provision_local(context, config)
        else:
            raise PlatformJobFailure(
                "sandbox_runner_provider_invalid",
                "The configured sandbox runner provider is not supported.",
            )
    except PlatformJobFailure:
        raise
    except Exception as exc:
        raise PlatformJobFailure(
            "sandbox_runner_provision_failed",
            "Sandbox runner provisioning failed; review the provider settings and job logs.",
            retryable=False,
        ) from exc

    await context.report("Sandbox runner connected", percent=100)
    return {"provider": provider, "runner_image": configured_runner_image(), **details}


async def _provision_cloudflare(
    context: PlatformJobContext,
    config: dict[str, Any],
) -> dict[str, object]:
    cloudflare = config.get("cloudflare")
    if not isinstance(cloudflare, dict):
        raise PlatformJobFailure(
            "cloudflare_settings_missing",
            "Cloudflare account settings are missing.",
        )
    account_id = _required(cloudflare, "account_id", "Cloudflare account ID")
    api_token = _required(cloudflare, "api_token", "Cloudflare API token")
    script_name = _required(cloudflare, "script_name", "Cloudflare Worker name")
    workflow_name = _required(cloudflare, "workflow_name", "Cloudflare Workflow name")

    await context.report("Verifying Cloudflare account access", percent=5)
    await _verify_cloudflare_account(account_id, api_token)
    await context.report("Copying the signed Builder image", percent=15)
    runner_image = await _mirror_runner_image_to_cloudflare(
        account_id=account_id,
        api_token=api_token,
    )
    await context.report("Deploying the Bifrost runner", percent=40)
    await _deploy_cloudflare_worker(
        account_id=account_id,
        api_token=api_token,
        script_name=script_name,
        workflow_name=workflow_name,
        runner_image=runner_image,
    )
    await _set_runtime_status(provisioned=True, connected=False)

    await context.report("Starting a runner self-test", percent=75)
    probe_id = await _run_cloudflare_probe_with_readiness_retry(
        context,
        account_id=account_id,
        api_token=api_token,
        workflow_name=workflow_name,
    )
    await _set_runtime_status(provisioned=True, connected=True)
    return {"external_run_id": probe_id, "runner_image": runner_image}


async def _provision_local(
    context: PlatformJobContext,
    config: dict[str, Any],
) -> dict[str, object]:
    # The local provider intentionally has no settings. The provider choice is
    # the complete configuration; the existing Worker proves connectivity via
    # the queue probe below.
    del config
    from src.core.cache.redis_client import get_redis
    from src.jobs.consumers.solution_builder_turn import QUEUE_NAME
    from src.jobs.rabbitmq import publish_message

    probe_id = uuid4().hex
    probe_key = f"bifrost:builder:probe:{probe_id}"
    await context.report("Testing the existing Bifrost Worker", percent=50)
    await publish_message(
        QUEUE_NAME,
        {"kind": "probe", "probe_id": probe_id},
        priority=9,
    )
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        async with get_redis() as redis:
            ready = await redis.get(probe_key)
            if ready:
                await redis.delete(probe_key)
                break
        await asyncio.sleep(0.5)
    else:
        raise PlatformJobFailure(
            "local_worker_probe_failed",
            "The existing Bifrost Worker did not answer the Builder queue probe.",
        )
    await _set_runtime_status(provisioned=True, connected=True)
    return {"uses_existing_worker": True}


async def _verify_cloudflare_account(account_id: str, api_token: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{_CLOUDFLARE_API_BASE}/accounts/{account_id}",
                headers={"Authorization": f"Bearer {api_token}"},
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PlatformJobFailure(
            "cloudflare_credentials_invalid",
            "Cloudflare could not verify the account and API token.",
        ) from exc
    if not isinstance(body, dict) or body.get("success") is not True:
        raise PlatformJobFailure(
            "cloudflare_credentials_invalid",
            "Cloudflare could not verify the account and API token.",
        )


async def _deploy_cloudflare_worker(
    *,
    account_id: str,
    api_token: str,
    script_name: str,
    workflow_name: str,
    runner_image: str,
) -> None:
    if (
        not _WRANGLER_NODE.is_file()
        or not _WRANGLER.is_file()
        or not _WORKER_SOURCE.is_file()
    ):
        raise PlatformJobFailure(
            "cloudflare_runner_assets_missing",
            "This Bifrost image does not contain the Cloudflare provisioning assets.",
        )
    config = {
        "name": script_name,
        # Let Wrangler bundle the mounted source at deploy time. Debug stacks
        # mount /app/src over the image, so a prebuilt dist/worker.js can be
        # older than worker.mjs even though production images build both.
        "main": str(_WORKER_SOURCE),
        "compatibility_date": "2026-08-01",
        "compatibility_flags": ["nodejs_compat"],
        "workers_dev": False,
        "observability": {"enabled": True},
        "containers": [
            {
                "class_name": "Sandbox",
                "image": runner_image,
                # The managed Builder image needs more than the lite tier's
                # 2 GB disk. Basic is the smallest Cloudflare tier that can
                # boot it reliably (4 GB disk, 1 GiB memory).
                "instance_type": "basic",
                "max_instances": 20,
                "ssh": {"enabled": False},
            }
        ],
        "durable_objects": {
            "bindings": [{"name": "Sandbox", "class_name": "Sandbox"}]
        },
        "migrations": [{"tag": "v1", "new_sqlite_classes": ["Sandbox"]}],
        "workflows": [
            {
                "binding": "BIFROST_BUILDER_WORKFLOW",
                "name": workflow_name,
                "class_name": "BifrostBuilderWorkflow",
            }
        ],
    }
    with tempfile.TemporaryDirectory(prefix="bifrost-cloudflare-runner-") as tmp:
        config_path = Path(tmp) / "wrangler.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        env = os.environ.copy()
        env["CLOUDFLARE_API_TOKEN"] = api_token
        env["CLOUDFLARE_ACCOUNT_ID"] = account_id
        process = await asyncio.create_subprocess_exec(
            str(_WRANGLER_NODE),
            str(_WRANGLER),
            "deploy",
            "--config",
            str(config_path),
            "--containers-rollout",
            "immediate",
            cwd=str(_CLOUDFLARE_PROJECT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
    if process.returncode != 0:
        safe_output = output.decode("utf-8", errors="replace")[-_OUTPUT_LIMIT:]
        safe_output = safe_output.replace(api_token, "[redacted]")
        logger.warning(
            "Cloudflare rejected the Builder runtime deployment: %s",
            safe_output[-2000:],
        )
        raise PlatformJobFailure(
            "cloudflare_deploy_failed",
            "Cloudflare rejected the Bifrost runner deployment. Review the "
            "Cloudflare token permissions and account limits, then try again.",
        )


async def _mirror_runner_image_to_cloudflare(
    *,
    account_id: str,
    api_token: str,
) -> str:
    """Copy the public signed image into one account without a Docker daemon."""

    if not _CRANE.is_file():
        raise PlatformJobFailure(
            "cloudflare_registry_copy_unavailable",
            "This Bifrost image does not contain the managed registry-copy tool.",
        )

    env = os.environ.copy()
    env["CLOUDFLARE_API_TOKEN"] = api_token
    env["CLOUDFLARE_ACCOUNT_ID"] = account_id
    credentials_process = await asyncio.create_subprocess_exec(
        str(_WRANGLER_NODE),
        str(_WRANGLER),
        "containers",
        "registries",
        "credentials",
        _CLOUDFLARE_REGISTRY,
        "--push",
        "--json",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await credentials_process.communicate()
    if credentials_process.returncode != 0:
        safe_output = output.decode("utf-8", errors="replace")[-_OUTPUT_LIMIT:]
        safe_output = safe_output.replace(api_token, "[redacted]")
        logger.warning(
            "Cloudflare registry credentials were rejected: %s",
            safe_output[-2000:],
        )
        raise PlatformJobFailure(
            "cloudflare_registry_credentials_failed",
            "Cloudflare could not issue temporary image-registry credentials. "
            "Confirm the token has Workers Containers Write permission.",
        )

    try:
        credentials = json.loads(output)
        username = credentials["username"]
        password = credentials["password"]
        if not isinstance(username, str) or not isinstance(password, str):
            raise TypeError
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PlatformJobFailure(
            "cloudflare_registry_credentials_invalid",
            "Cloudflare returned invalid temporary image-registry credentials.",
        ) from exc

    source = configured_runner_image()
    destination = cloudflare_runner_image(account_id)
    with tempfile.TemporaryDirectory(prefix="bifrost-cloudflare-registry-") as tmp:
        copy_env = os.environ.copy()
        copy_env["DOCKER_CONFIG"] = tmp
        login = await asyncio.create_subprocess_exec(
            str(_CRANE),
            "auth",
            "login",
            _CLOUDFLARE_REGISTRY,
            "--username",
            username,
            "--password-stdin",
            env=copy_env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        login_output, _ = await login.communicate(password.encode("utf-8"))
        if login.returncode != 0:
            logger.warning(
                "Cloudflare registry login failed: %s",
                login_output.decode("utf-8", errors="replace")[-2000:],
            )
            raise PlatformJobFailure(
                "cloudflare_registry_login_failed",
                "Cloudflare issued registry credentials but its registry rejected them.",
            )

        copy = await asyncio.create_subprocess_exec(
            str(_CRANE),
            "copy",
            "--platform",
            "linux/amd64",
            "--no-clobber",
            source,
            destination,
            env=copy_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        copy_output, _ = await copy.communicate()
        if copy.returncode != 0:
            safe_output = copy_output.decode("utf-8", errors="replace")[-_OUTPUT_LIMIT:]
            if _is_exact_destination_no_clobber(output=safe_output, destination=destination):
                logger.info(
                    "Cloudflare registry already contains immutable Builder image tag %s",
                    destination,
                )
                return destination
            logger.warning(
                "Cloudflare registry image copy failed: %s",
                safe_output[-2000:],
            )
            raise PlatformJobFailure(
                "cloudflare_registry_copy_failed",
                "The signed Bifrost Builder image could not be copied into the "
                "Cloudflare account registry.",
            )
    return destination


def _is_exact_destination_no_clobber(*, output: str, destination: str) -> bool:
    return (
        f"refusing to clobber existing tag {destination}@sha256:" in output
        and "Error:" in output
    )


async def _start_cloudflare_probe(
    account_id: str,
    api_token: str,
    workflow_name: str,
) -> str:
    # The Sandbox SDK caps sandbox IDs at 63 characters. Keep the Workflow
    # instance ID short enough that the Worker can safely reuse or prefix it.
    probe_id = f"bifrost-probe-{uuid4().hex}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{_CLOUDFLARE_API_BASE}/accounts/{account_id}/workflows/"
                f"{workflow_name}/instances",
                headers={"Authorization": f"Bearer {api_token}"},
                json={
                    "instance_id": probe_id,
                    "params": {"mode": "probe", "probe_id": probe_id},
                },
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PlatformJobFailure(
            "cloudflare_probe_start_failed",
            "Cloudflare deployed the runner but could not start its self-test.",
        ) from exc
    result = body.get("result") if isinstance(body, dict) else None
    external_id = result.get("id") if isinstance(result, dict) else None
    if body.get("success") is not True or not isinstance(external_id, str):
        raise PlatformJobFailure(
            "cloudflare_probe_start_failed",
            "Cloudflare returned an invalid self-test response.",
        )
    return external_id


async def _run_cloudflare_probe_with_readiness_retry(
    context: PlatformJobContext,
    *,
    account_id: str,
    api_token: str,
    workflow_name: str,
) -> str:
    deadline = asyncio.get_running_loop().time() + _PROBE_TIMEOUT_SECONDS
    backoffs = iter(_ROLLOUT_PROBE_BACKOFF_SECONDS)
    probe_id = await _start_cloudflare_probe(account_id, api_token, workflow_name)
    while True:
        try:
            await _wait_for_cloudflare_probe(
                context,
                account_id=account_id,
                api_token=api_token,
                workflow_name=workflow_name,
                probe_id=probe_id,
                deadline=deadline,
            )
            return probe_id
        except _CloudflareRolloutNotReady:
            try:
                backoff = next(backoffs)
            except StopIteration as exc:
                raise PlatformJobFailure(
                    "cloudflare_probe_rollout_not_ready",
                    "Cloudflare deployed the runner, but the Worker did not "
                    "become available to its Sandbox workflow before the "
                    "readiness deadline. Wait a few minutes and try again.",
                ) from exc

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            sleep_for = min(backoff, max(0.0, remaining))
            await context.report(
                "Cloudflare is finishing the Worker and container rollout; "
                "retrying the runner self-test",
                percent=85,
            )
            await asyncio.sleep(sleep_for)
            if asyncio.get_running_loop().time() >= deadline:
                break
            await context.report("Starting a fresh runner self-test", percent=85)
            probe_id = await _start_cloudflare_probe(
                account_id,
                api_token,
                workflow_name,
            )
    raise PlatformJobFailure(
        "cloudflare_probe_timeout",
        "The Cloudflare runner self-test did not finish within fifteen minutes.",
    )


async def _wait_for_cloudflare_probe(
    context: PlatformJobContext,
    *,
    account_id: str,
    api_token: str,
    workflow_name: str,
    probe_id: str,
    deadline: float | None = None,
) -> None:
    if deadline is None:
        deadline = asyncio.get_running_loop().time() + _PROBE_TIMEOUT_SECONDS
    url = (
        f"{_CLOUDFLARE_API_BASE}/accounts/{account_id}/workflows/"
        f"{workflow_name}/instances/{probe_id}"
    )
    while asyncio.get_running_loop().time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {api_token}"},
                    params={"simple": "true"},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PlatformJobFailure(
                "cloudflare_probe_failed",
                "Cloudflare could not report the runner self-test status.",
            ) from exc
        result = body.get("result") if isinstance(body, dict) else None
        state = result.get("status") if isinstance(result, dict) else None
        if (
            state == "complete"
            and isinstance(result, dict)
            and result.get("success") is True
        ):
            return
        if state in {"errored", "terminated"}:
            details = await _fetch_cloudflare_probe_details(
                account_id=account_id,
                api_token=api_token,
                workflow_name=workflow_name,
                probe_id=probe_id,
            )
            detail_result = details.get("result") if isinstance(details, dict) else None
            if _is_cloudflare_rollout_not_ready(detail_result):
                raise _CloudflareRolloutNotReady
            raise PlatformJobFailure(
                "cloudflare_probe_failed",
                "The Cloudflare runner self-test failed to start the Bifrost image.",
            )
        await context.report("Waiting for the runner self-test", percent=85)
        await asyncio.sleep(3)
    raise PlatformJobFailure(
        "cloudflare_probe_timeout",
        "The Cloudflare runner self-test did not finish within fifteen minutes.",
    )


async def _fetch_cloudflare_probe_details(
    *,
    account_id: str,
    api_token: str,
    workflow_name: str,
    probe_id: str,
) -> dict[str, Any]:
    url = (
        f"{_CLOUDFLARE_API_BASE}/accounts/{account_id}/workflows/"
        f"{workflow_name}/instances/{probe_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_token}"},
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PlatformJobFailure(
            "cloudflare_probe_failed",
            "Cloudflare could not report the runner self-test status.",
        ) from exc
    if not isinstance(body, dict):
        raise PlatformJobFailure(
            "cloudflare_probe_failed",
            "Cloudflare returned an invalid runner self-test status response.",
        )
    return body


def _is_cloudflare_rollout_not_ready(result: object) -> bool:
    if not isinstance(result, dict):
        return False
    error = result.get("error")
    if not isinstance(error, dict):
        return False
    name = error.get("name")
    message = error.get("message")
    if (
        result.get("step_count") == 0
        and name == "Error"
        and message == "Worker not found."
    ):
        return True
    container_starting_message = (
        "SandboxError: Container is starting. Please retry in a moment."
    )
    return (
        name == "NonRetryableError" and message == container_starting_message
    ) or (
        name == "Error"
        and message == f"NonRetryableError: {container_starting_message}"
    )


async def _set_runtime_status(*, provisioned: bool, connected: bool) -> None:
    async with get_db_context() as db:
        await SandboxRunnerConfigService(db).set_runtime_status(
            provisioned=provisioned,
            connected=connected,
            updated_by="sandbox-runner-provisioning",
        )
        await db.commit()


def _required(source: dict[str, Any], key: str, label: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PlatformJobFailure(
            "sandbox_runner_settings_incomplete",
            f"{label} is required.",
        )
    return value.strip()


__all__ = ["configured_runner_image", "provision_configured_runner"]
