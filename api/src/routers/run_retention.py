"""
Run Retention Router

The platform-wide run retention window, readable by any signed-in user so
pages can explain why a finished run is no longer available.
"""

from fastapi import APIRouter

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.run_retention import RunRetentionPublic
from src.services.run_retention.settings import RunRetentionSettingsService

router = APIRouter(prefix="/api/run-retention", tags=["Run Retention"])


@router.get("", response_model=RunRetentionPublic, summary="Get the run retention window")
async def get_run_retention(user: CurrentActiveUser, db: DbSession) -> RunRetentionPublic:
    """The retention window, for wording about removed runs. Any signed-in user."""
    settings = await RunRetentionSettingsService(db).get_settings()
    return RunRetentionPublic(days=settings.days)
