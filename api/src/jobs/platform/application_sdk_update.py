"""Platform job for rebuilding one standalone App against the current SDK."""

from __future__ import annotations

from shared.models import (
    ApplicationSdkUpdatePayload,
    ApplicationSdkUpdatePayloadV1,
    ApplicationSdkUpdatePayloadV2,
)
from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobFailure,
    define_platform_job,
)
from src.models.orm.applications import Application
from src.services.application_build import rebuild_application_from_source
from src.services.application_source_resolver import (
    ApplicationSourceUnavailable,
    resolve_application_source,
)


async def run_application_sdk_update(
    context: PlatformJobContext,
    payload: ApplicationSdkUpdatePayloadV1,
) -> dict[str, str]:
    await context.report("Loading App")
    async with get_db_context() as db:
        if isinstance(payload, ApplicationSdkUpdatePayloadV2) and payload.automatic_upgrade:
            from shared.app_sdk_upgrade_settings import AppSdkUpgradeSettingsService

            if not (await AppSdkUpgradeSettingsService(db).get_settings()).enabled:
                raise PlatformJobFailure(
                    "automatic_sdk_upgrades_disabled",
                    "Automatic App SDK upgrades were disabled. Use Update SDK to retry manually.",
                )
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
    if isinstance(payload, ApplicationSdkUpdatePayloadV2):
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
            target_sdk_fingerprint=payload.target_sdk_fingerprint,
            target_sdk_contract_version=payload.target_sdk_contract_version,
            retain_previous_deployment=payload.automatic_upgrade,
            automatic_upgrade=payload.automatic_upgrade,
            expected_sdk_binding_api_major=(
                payload.expected_sdk_binding_api_major if isinstance(payload, ApplicationSdkUpdatePayload) else None
            ),
            target_sdk_binding_api_major=(
                payload.target_sdk_binding_api_major if isinstance(payload, ApplicationSdkUpdatePayload) else None
            ),
        )
    else:
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


APPLICATION_SDK_UPDATE_DEFINITION = define_platform_job(
    "application.sdk_update",
    ApplicationSdkUpdatePayload,
    run_application_sdk_update,
    historical_payload_models={1: ApplicationSdkUpdatePayloadV1, 2: ApplicationSdkUpdatePayloadV2},
)
