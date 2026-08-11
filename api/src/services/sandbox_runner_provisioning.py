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
_WORKER_BUNDLE = _CLOUDFLARE_PROJECT / "dist" / "worker.js"
_PROBE_TIMEOUT_SECONDS = 4 * 60
_OUTPUT_LIMIT = 32_000
logger = logging.getLogger(__name__)


def configured_runner_image() -> str:
    settings = get_settings()
    tag = settings.builder_runner_image_tag or get_version().removeprefix("v")
    if tag in {"", "unknown", "debug"}:
        tag = "dev"
    tag = re.sub(r"[^A-Za-z0-9_.-]", "-", tag)[:128]
    return f"{settings.builder_runner_image_repository}:{tag}"


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
    await context.report("Deploying the Bifrost runner", percent=20)
    await _deploy_cloudflare_worker(
        account_id=account_id,
        api_token=api_token,
        script_name=script_name,
        workflow_name=workflow_name,
    )
    await _set_runtime_status(provisioned=True, connected=False)

    await context.report("Starting a runner self-test", percent=70)
    probe_id = await _start_cloudflare_probe(
        account_id,
        api_token,
        workflow_name,
    )
    await _wait_for_cloudflare_probe(
        context,
        account_id=account_id,
        api_token=api_token,
        workflow_name=workflow_name,
        probe_id=probe_id,
    )
    await _set_runtime_status(provisioned=True, connected=True)
    return {"external_run_id": probe_id}


async def _provision_local(
    context: PlatformJobContext,
    config: dict[str, Any],
) -> dict[str, object]:
    local = config.get("local")
    if not isinstance(local, dict):
        raise PlatformJobFailure(
            "local_runner_settings_missing",
            "Local runner settings are missing.",
        )
    endpoint = _required(local, "endpoint_url", "Local runner endpoint").rstrip("/")
    secret = _required(local, "runner_secret", "Local runner secret")
    headers = {"Authorization": f"Bearer {secret}"}
    await context.report("Provisioning the local runner", percent=25)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{endpoint}/provision",
                headers=headers,
                json={"runner_image": configured_runner_image()},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PlatformJobFailure(
            "local_runner_provision_failed",
            "The local runner did not accept provisioning.",
        ) from exc
    await _set_runtime_status(provisioned=True, connected=False)
    await context.report("Testing the local runner", percent=75)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{endpoint}/health", headers=headers)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PlatformJobFailure(
            "local_runner_probe_failed",
            "The local runner did not pass its connectivity test.",
        ) from exc
    if not isinstance(body, dict) or body.get("ready") is not True:
        raise PlatformJobFailure(
            "local_runner_probe_failed",
            "The local runner reported that it is not ready.",
        )
    await _set_runtime_status(provisioned=True, connected=True)
    return {}


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
) -> None:
    if (
        not _WRANGLER_NODE.is_file()
        or not _WRANGLER.is_file()
        or not _WORKER_BUNDLE.is_file()
    ):
        raise PlatformJobFailure(
            "cloudflare_runner_assets_missing",
            "This Bifrost image does not contain the Cloudflare provisioning assets.",
        )
    config = {
        "name": script_name,
        "main": str(_WORKER_BUNDLE),
        "compatibility_date": "2026-08-01",
        "compatibility_flags": ["nodejs_compat"],
        "workers_dev": False,
        "observability": {"enabled": True},
        "containers": [
            {
                "class_name": "Sandbox",
                "image": configured_runner_image(),
                # The OpenCode sandbox image unpacks beyond the lite tier's
                # 2 GB disk. Basic is the smallest Cloudflare tier that can
                # boot the managed Builder image (4 GB disk, 1 GiB memory).
                "instance_type": "basic",
                "max_instances": 20,
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


async def _wait_for_cloudflare_probe(
    context: PlatformJobContext,
    *,
    account_id: str,
    api_token: str,
    workflow_name: str,
    probe_id: str,
) -> None:
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
            raise PlatformJobFailure(
                "cloudflare_probe_failed",
                "The Cloudflare runner self-test failed to start the Bifrost image.",
            )
        await context.report("Waiting for the runner self-test", percent=85)
        await asyncio.sleep(3)
    raise PlatformJobFailure(
        "cloudflare_probe_timeout",
        "The Cloudflare runner self-test did not finish within four minutes.",
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
