"""
Permissions Router

The permission catalog the roles and access screens are built from.
"""

from fastapi import APIRouter

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.permissions import PermissionCatalogEntry
from src.services.authorization.enforce import GLOBAL, authorize_operation
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
    db: DbSession,
) -> list[PermissionCatalogEntry]:
    await authorize_operation(db, user, "GET /api/permissions/catalog", GLOBAL)
    return build_catalog()
