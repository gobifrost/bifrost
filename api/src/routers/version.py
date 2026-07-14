import logging

from fastapi import APIRouter
from pydantic import BaseModel

from shared.contract_version import get_contract_version
from shared.version import get_version
from src.services.sdk_package import sdk_contract_version, sdk_fingerprint

router = APIRouter(prefix="/api/version", tags=["version"])

logger = logging.getLogger(__name__)


class VersionResponse(BaseModel):
    version: str
    contract_version: int
    sdk_fingerprint: str
    sdk_contract_version: int


def get_sdk_fingerprint() -> str:
    """SDK content fingerprint, degrading to ``"unavailable"`` rather than
    failing the whole /api/version response.

    ``sdk_fingerprint`` shells out to node/esbuild on first call per version
    (lru_cached thereafter via ``_built_bundle``); a broken node toolchain in
    this environment must not take down an otherwise-healthy version
    endpoint. Broad except is intentional here: any failure of the build
    subprocess (missing binary, timeout, non-zero exit, ...) should degrade
    the same way, and the exception is logged so the underlying cause is
    still visible.
    """
    try:
        return sdk_fingerprint(get_version())
    except Exception:  # noqa: BLE001 - build-toolchain failure must degrade, not 500 /api/version; logged below
        logger.exception("failed to compute SDK fingerprint")
        return "unavailable"


@router.get("", response_model=VersionResponse)
async def get_version_info() -> VersionResponse:
    return VersionResponse(
        version=get_version(),
        contract_version=get_contract_version(),
        sdk_fingerprint=get_sdk_fingerprint(),
        sdk_contract_version=sdk_contract_version(),
    )
