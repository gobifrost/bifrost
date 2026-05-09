from fastapi import APIRouter

from shared.models import VersionResponse
from shared.version import get_version

router = APIRouter(prefix="/api/version", tags=["version"])


@router.get("", response_model=VersionResponse)
async def get_version_info() -> VersionResponse:
    return VersionResponse(
        version=get_version(),
    )
