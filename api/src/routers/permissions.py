"""
Permissions Router

The permission catalog the roles and access screens are built from,
readable by any signed-in user (it is vocabulary, the same for everyone).
"""

from fastapi import APIRouter

from src.core.auth import CurrentActiveUser
from src.models.contracts.permissions import PermissionCatalogEntry
from src.services.permission_catalog import build_catalog

router = APIRouter(prefix="/api/permissions", tags=["Permissions"])


@router.get(
    "/catalog",
    response_model=list[PermissionCatalogEntry],
    summary="List permission domains",
    description="Every permission domain with its title, area, guidance, actions, scope and enforcement",
)
async def get_permission_catalog(
    user: CurrentActiveUser,
) -> list[PermissionCatalogEntry]:
    return build_catalog()
