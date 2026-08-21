"""ROI settings API endpoints."""

import logging

from fastapi import APIRouter, status

from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.models.contracts.roi import (
    ROISettingsRequest,
    ROISettingsResponse,
)
from src.services.audit import emit_audit
from src.services.authorization import CurrentAuthorizationContext
from src.services.roi_settings_service import ROISettingsService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/roi",
    tags=["ROI Settings"],
)


def _require_roi_settings(
    authorization: CurrentAuthorizationContext,
    capability: str = "metrics.read",
) -> None:
    authorization.require(capability)
    authorization.require_resource_boundary(None)


@router.get("/settings")
async def get_roi_settings(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> ROISettingsResponse:
    """
    Get current ROI settings.

    Returns current settings or defaults if not configured.
    """
    _require_roi_settings(authorization)

    service = ROISettingsService(db)
    settings = await service.get_settings()

    return ROISettingsResponse(
        time_saved_unit=settings.time_saved_unit,
        value_unit=settings.value_unit,
    )


@router.post("/settings", status_code=status.HTTP_200_OK)
async def update_roi_settings(
    request: ROISettingsRequest,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> ROISettingsResponse:
    """
    Update ROI settings.
    """
    _require_roi_settings(authorization, "metrics.readwrite")

    service = ROISettingsService(db)
    actor_email = authorization.effective_actor.email

    settings = await service.save_settings(
        time_saved_unit=request.time_saved_unit,
        value_unit=request.value_unit,
        updated_by=actor_email,
    )
    await emit_audit(
        db,
        "roi_settings.update",
        resource_type="roi_settings",
        details={
            "time_saved_unit": request.time_saved_unit,
            "value_unit": request.value_unit,
        },
    )

    await db.commit()

    logger.info(
        f"ROI settings updated by {actor_email}: "
        f"time_saved_unit={log_safe(request.time_saved_unit)}, value_unit={log_safe(request.value_unit)}"
    )

    return ROISettingsResponse(
        time_saved_unit=settings.time_saved_unit,
        value_unit=settings.value_unit,
    )
