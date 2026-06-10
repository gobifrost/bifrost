"""ROI settings API endpoints."""

import logging

from fastapi import APIRouter, status

from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.models.contracts.roi import (
    ROISettingsRequest,
    ROISettingsResponse,
)
from src.services.roi_settings_service import ROISettingsService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/roi",
    tags=["ROI Settings"],
    dependencies=[RequirePlatformAdmin],  # All endpoints require platform admin
)


@router.get("/settings")
async def get_roi_settings(
    db: DbSession,
    user: CurrentActiveUser,
) -> ROISettingsResponse:
    """
    Get current ROI settings.

    Returns current settings or defaults if not configured.
    Requires platform admin access.
    """
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
    user: CurrentActiveUser,
) -> ROISettingsResponse:
    """
    Update ROI settings.

    Requires platform admin access.
    """
    service = ROISettingsService(db)

    settings = await service.save_settings(
        time_saved_unit=request.time_saved_unit,
        value_unit=request.value_unit,
        updated_by=user.email,
    )

    await db.commit()

    logger.info(
        f"ROI settings updated by {user.email}: "
        f"time_saved_unit={log_safe(request.time_saved_unit)}, value_unit={log_safe(request.value_unit)}"
    )

    return ROISettingsResponse(
        time_saved_unit=settings.time_saved_unit,
        value_unit=settings.value_unit,
    )
