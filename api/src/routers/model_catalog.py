"""Read-only model catalog lookups for any signed-in user."""

from fastapi import APIRouter

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.ai_models import ModelDisplayNamesResponse
from src.services.model_pricing import used_model_display_names

router = APIRouter(prefix="/api/model-catalog", tags=["Model Catalog"])


@router.get("/names")
async def get_model_display_names(
    db: DbSession, user: CurrentActiveUser
) -> ModelDisplayNamesResponse:
    """Display names for the model ids shown in runs, usage, and settings."""
    del user
    return ModelDisplayNamesResponse(names=await used_model_display_names(db))
