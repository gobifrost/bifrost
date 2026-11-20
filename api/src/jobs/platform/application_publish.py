"""Application publishing as a standardized platform-job handler."""

from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.core.pubsub import publish_app_published
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobFailure,
    define_platform_job,
)
from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository
from src.services.app_bundler import build_snapshot_using_file_compute

APPLICATION_PUBLISH_JOB_TYPE = "application.publish"
logger = logging.getLogger(__name__)


class ApplicationPublishPayload(BaseModel):
    application_id: UUID
    message: str | None = None


async def run_application_publish(
    context: PlatformJobContext,
    raw_payload: BaseModel,
) -> dict[str, object]:
    payload = ApplicationPublishPayload.model_validate(raw_payload)
    if context.file_compute is None:
        raise PlatformJobFailure(
            "file_compute_unavailable",
            "Application publishing requires an App bundle compute worker.",
        )
    try:
        await context.report("Building current source", percent=5)
        async with get_db_context() as db:
            if not await context.authorize_effect(db):
                from src.jobs.platform.base import PlatformJobCancelled

                raise PlatformJobCancelled
            application = await db.get(Application, payload.application_id)
            if application is None:
                raise PlatformJobFailure("application_not_found", "Application no longer exists.")
            repo = ApplicationRepository(
                db, application.organization_id, user_id=context.requested_by_user_id,
                is_superuser=True,
            )
            snapshot = await repo.prepare_publish(application.id)
            await db.commit()
        if snapshot is None:
            raise PlatformJobFailure("application_not_found", "Application no longer exists.")

        # The database context above is intentionally closed before the
        # CPU-bound transfer. The worker sees exactly this API snapshot.
        bundle_result, artifacts = await build_snapshot_using_file_compute(
            str(snapshot.application_id),
            snapshot.source_files,
            snapshot.dependencies,
            context.file_compute,
        )
        if not bundle_result.success:
            first_err = (bundle_result.errors or [None])[0]
            raise ValueError(
                "Bundle build failed during publish: "
                f"{first_err.text if first_err else 'unknown error'}"
            )

        await context.report("Promoting current bundle", percent=80)
        async with get_db_context() as db:
            repo = ApplicationRepository(
                db, snapshot.organization_id, user_id=context.requested_by_user_id,
                is_superuser=True,
            )
            published = await repo.promote_publish_snapshot(
                snapshot,
                bundle_result,
                artifacts,
                effect_authorization=context.authorize_effect,
            )
            if published is None:
                raise PlatformJobFailure(
                    "application_not_found",
                    "Application no longer exists.",
                )

            published_at = (
                published.published_at.isoformat()
                if published.published_at is not None
                else None
            )
            files_published = len(published.published_snapshot or {})
            await db.commit()

        await context.report("Published App", percent=100)
        result: dict[str, object] = {
            "application_id": str(payload.application_id),
            "published_at": published_at,
            "files_published": files_published,
        }
        try:
            await publish_app_published(
                app_id=str(payload.application_id),
                user_id=context.requested_by_user_id,
                user_name=context.requested_by_name,
                new_version_id=published_at or "",
            )
        except Exception:
            logger.warning(
                "Published app but failed to broadcast its live update",
                extra={"application_id": str(payload.application_id)},
                exc_info=True,
            )
        return result
    except PlatformJobFailure:
        raise
    except ValueError as exc:
        raise PlatformJobFailure(
            "application_publish_failed",
            str(exc),
            retryable=False,
        ) from exc


APPLICATION_PUBLISH_DEFINITION = define_platform_job(
    APPLICATION_PUBLISH_JOB_TYPE, ApplicationPublishPayload, run_application_publish
)
