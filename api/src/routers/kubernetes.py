"""Global Settings -> Kubernetes: worker scaling and isolated jobs."""

import os

from fastapi import APIRouter, HTTPException, status

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.kubernetes import (
    KubernetesExecutionSettings,
    KubernetesExecutionUpdate,
    KubernetesPolicyUpdate,
    KubernetesStatus,
)
from src.services.kubernetes_execution import KubernetesExecutionService
from src.services.authorization.enforce import authorize_operation
from src.services.authorization.evaluator import GLOBAL

KUBERNETES_STATUS_OPERATION = "GET /api/admin/kubernetes/status"
KUBERNETES_EXECUTION_GET_OPERATION = "GET /api/admin/kubernetes/execution"
KUBERNETES_EXECUTION_UPDATE_OPERATION = "PUT /api/admin/kubernetes/execution"
KUBERNETES_POLICY_UPDATE_OPERATION = "PUT /api/admin/kubernetes/policy"


async def _authorize_platform_setting(
    db: DbSession,
    user: CurrentActiveUser,
    operation: str,
) -> None:
    await authorize_operation(db, user, operation, GLOBAL)


admin_router = APIRouter(
    prefix="/api/admin/kubernetes",
    tags=["Kubernetes"],
)


def _service(db: DbSession) -> KubernetesExecutionService:
    return KubernetesExecutionService(db)


@admin_router.get("/status", response_model=KubernetesStatus)
async def get_kubernetes_status(
    db: DbSession, user: CurrentActiveUser
) -> KubernetesStatus:
    await _authorize_platform_setting(db, user, KUBERNETES_STATUS_OPERATION)
    return await KubernetesExecutionService.status()


@admin_router.get("/execution", response_model=KubernetesExecutionSettings)
async def get_kubernetes_execution(
    db: DbSession, user: CurrentActiveUser
) -> KubernetesExecutionSettings:
    await _authorize_platform_setting(db, user, KUBERNETES_EXECUTION_GET_OPERATION)
    return await _service(db).describe(os.environ)


@admin_router.put("/execution", response_model=KubernetesExecutionSettings)
async def update_kubernetes_execution(
    request: KubernetesExecutionUpdate,
    db: DbSession,
    current_user: CurrentActiveUser,
) -> KubernetesExecutionSettings:
    await _authorize_platform_setting(
        db, current_user, KUBERNETES_EXECUTION_UPDATE_OPERATION
    )
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
    return await service.describe(os.environ)


@admin_router.put("/policy", response_model=KubernetesExecutionSettings)
async def update_kubernetes_policy(
    request: KubernetesPolicyUpdate,
    db: DbSession,
    current_user: CurrentActiveUser,
) -> KubernetesExecutionSettings:
    await _authorize_platform_setting(
        db, current_user, KUBERNETES_POLICY_UPDATE_OPERATION
    )
    service = _service(db)
    try:
        await service.update_policy(
            request.model_dump(exclude_unset=True),
            updated_by=current_user.email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    await db.commit()
    return await service.describe(os.environ)
