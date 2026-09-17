"""Platform-admin Kubernetes elastic execution settings."""

from fastapi import APIRouter, HTTPException, status

from src.config import get_settings
from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.core.db_deps import DbSession
from src.models.contracts.kubernetes import (
    KubernetesExecutionSettings,
    KubernetesExecutionUpdate,
    KubernetesStatus,
)
from src.services.kubernetes_execution import KubernetesExecutionService

admin_router = APIRouter(
    prefix="/api/admin/kubernetes",
    tags=["Kubernetes"],
    dependencies=[RequirePlatformAdmin],
)


def _service(db: DbSession) -> KubernetesExecutionService:
    return KubernetesExecutionService(db)


@admin_router.get("/status", response_model=KubernetesStatus)
async def get_kubernetes_status() -> KubernetesStatus:
    return await KubernetesExecutionService.status(get_settings())


@admin_router.get("/execution", response_model=KubernetesExecutionSettings)
async def get_kubernetes_execution(db: DbSession) -> KubernetesExecutionSettings:
    return await _service(db).list_job_types(get_settings())


@admin_router.put("/execution", response_model=KubernetesExecutionSettings)
async def update_kubernetes_execution(
    request: KubernetesExecutionUpdate,
    db: DbSession,
    current_user: CurrentActiveUser,
) -> KubernetesExecutionSettings:
    service = _service(db)
    try:
        await service.set_job_type_enabled(
            request.job_type,
            request.enabled,
            updated_by=current_user.email,
        )
        if "max_concurrency" in request.model_fields_set:
            await service.set_job_type_concurrency(
                request.job_type,
                request.max_concurrency,
                updated_by=current_user.email,
            )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    await db.commit()
    return await service.list_job_types(get_settings())
