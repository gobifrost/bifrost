"""Admin-requested sandbox runner provisioning PlatformJob."""

from typing import Literal

from pydantic import BaseModel

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobPolicy,
)
from src.services.sandbox_runner_provisioning import provision_configured_runner


class SandboxRunnerProvisionPayload(BaseModel):
    provider: Literal["cloudflare", "local"]


async def run_sandbox_runner_provision(
    context: PlatformJobContext,
    raw_payload: BaseModel,
) -> dict[str, object]:
    payload = SandboxRunnerProvisionPayload.model_validate(raw_payload)
    result = await provision_configured_runner(context)
    if result.get("provider") != payload.provider:
        from src.jobs.platform.base import PlatformJobFailure

        raise PlatformJobFailure(
            "sandbox_runner_configuration_changed",
            "Sandbox runner settings changed after this setup job was queued; run setup again.",
        )
    return result


SANDBOX_RUNNER_PROVISION_DEFINITION = PlatformJobDefinition(
    job_type="sandbox.runner.provision",
    payload_version=1,
    payload_model=SandboxRunnerProvisionPayload,
    handler=run_sandbox_runner_provision,
    policy=PlatformJobPolicy(
        timeout_seconds=15 * 60,
        max_attempts=1,
        max_concurrency=1,
        retry_on_runner_loss=False,
        min_memory_headroom_mb=128,
    ),
    encrypt_payload=True,
)


__all__ = [
    "SANDBOX_RUNNER_PROVISION_DEFINITION",
    "SandboxRunnerProvisionPayload",
]
