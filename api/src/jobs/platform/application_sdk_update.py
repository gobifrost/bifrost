"""Platform job for rebuilding one standalone App against the current SDK."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.orm.applications import Application
from src.services.application_build import rebuild_application_from_source
from src.services.application_source_resolver import (
    ApplicationSourceUnavailable,
    resolve_application_source,
)


class ApplicationSdkUpdatePayload(BaseModel):
    application_id: UUID
    deployment_id: UUID = Field(default_factory=uuid4)
    expected_active_deployment_id: UUID | None = None
    expected_sdk_package_version: str | None = None
    expected_sdk_fingerprint: str | None = None
    expected_sdk_contract_version: int | None = None
    expected_sdk_built_at: datetime | None = None


async def run_application_sdk_update(
    context: PlatformJobContext, payload: ApplicationSdkUpdatePayload
) -> dict[str, str]:
    await context.report("Loading App")
    async with get_db_context() as db:
        app = await db.get(Application, payload.application_id)
        if app is None or app.app_model != "standalone_v2":
            raise PlatformJobFailure(
                "app_not_updateable",
                "The App no longer supports SDK updates.",
            )

    await context.report("Resolving retained source")
    try:
        source = await resolve_application_source(app)
    except ApplicationSourceUnavailable as exc:
        raise PlatformJobFailure(exc.code, str(exc)) from exc

    await context.report("Rebuilding App")
    result = await rebuild_application_from_source(
        application=app,
        deployment_id=payload.deployment_id,
        source=source,
        expected_active_deployment_id=payload.expected_active_deployment_id,
        expected_sdk_package_version=payload.expected_sdk_package_version,
        expected_sdk_fingerprint=payload.expected_sdk_fingerprint,
        expected_sdk_contract_version=payload.expected_sdk_contract_version,
        expected_sdk_built_at=payload.expected_sdk_built_at,
        enforce_expected_state=True,
    )
    await context.report("App SDK updated", percent=100)
    return result


APPLICATION_SDK_UPDATE_DEFINITION = PlatformJobDefinition(
    job_type="application.sdk_update",
    payload_version=1,
    payload_model=ApplicationSdkUpdatePayload,
    handler=run_application_sdk_update,
    policy=PlatformJobPolicy(
        timeout_seconds=20 * 60,
        max_attempts=1,
        max_concurrency=1,
        min_memory_headroom_mb=512,
        execution_class="build",
    ),
    display_name="App SDK rebuilds",
    description=(
        "Rebuild the web SDK and republish affected apps in isolated pods "
        "instead of the scheduler."
    ),
)
