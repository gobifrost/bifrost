"""Application publishing as a standardized platform-job handler."""

from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.core.pubsub import publish_app_published
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository

APPLICATION_PUBLISH_JOB_TYPE = "application.publish"
logger = logging.getLogger(__name__)


class ApplicationPublishPayload(BaseModel):
    application_id: UUID
    message: str | None = None


def _publish_percent(
    phase: str,
    current: int,
    total: int | None,
) -> float:
    if phase == "building current source":
        return 5
    if phase == "promoting current bundle":
        if not total:
            return 10
        return min(95, 10 + round(85 * current / total))
    if phase == "recording published version":
        return 98
    return 0


async def run_application_publish(
    context: PlatformJobContext,
    raw_payload: BaseModel,
) -> dict[str, object]:
    payload = ApplicationPublishPayload.model_validate(raw_payload)
    try:
        async with get_db_context() as db:
            application = await db.get(Application, payload.application_id)
            if application is None:
                raise PlatformJobFailure(
                    "application_not_found",
                    "Application no longer exists.",
                )

            repo = ApplicationRepository(
                db,
                application.organization_id,
                user_id=context.requested_by_user_id,
                is_superuser=True,
            )
            last_phase: str | None = None
            last_reported = -1

            async def report(
                phase: str,
                current: int,
                total: int | None,
            ) -> None:
                nonlocal last_phase, last_reported
                report_step = max(1, (total or 1) // 100)
                should_report = (
                    phase != last_phase
                    or current == 0
                    or (total is not None and current == total)
                    or current - last_reported >= report_step
                )
                if not should_report:
                    return
                await context.report(
                    phase,
                    current,
                    total,
                    _publish_percent(phase, current, total),
                )
                last_phase = phase
                last_reported = current

            published = await repo.publish(
                application.id,
                context.requested_by_email,
                payload.message,
                progress_callback=report,
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


APPLICATION_PUBLISH_DEFINITION = PlatformJobDefinition(
    job_type=APPLICATION_PUBLISH_JOB_TYPE,
    payload_version=1,
    payload_model=ApplicationPublishPayload,
    handler=run_application_publish,
    policy=PlatformJobPolicy(
        timeout_seconds=20 * 60,
        max_attempts=2,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=256,
    ),
)
