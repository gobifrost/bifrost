from fastapi import APIRouter
from pydantic import BaseModel

from shared.contract_version import get_contract_version
from shared.version import get_version

router = APIRouter(prefix="/api/version", tags=["version"])


class VersionResponse(BaseModel):
    version: str
    contract_version: int


@router.get("", response_model=VersionResponse)
async def get_version_info() -> VersionResponse:
    return VersionResponse(
        version=get_version(),
        contract_version=get_contract_version(),
    )
