"""Global Workers settings for installation-owned capacity providers."""

from fastapi import APIRouter, HTTPException

from shared.models import (
    CloudflareCapacityConnectAccepted, CloudflareCapacityConnectRequest,
    CloudflareCapacityStatus,
    CloudflareCapacityRules,
)
from src.config import get_settings
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.services.authorization.enforce import authorize_operation
from src.services.authorization.evaluator import GLOBAL
from src.services.platform_jobs import publish_platform_job_update
from src.services.worker_pool.cloudflare_setup import describe_cloudflare_capacity, enqueue_cloudflare_setup, update_cloudflare_rules
from src.services.worker_pool.connection import CapacityConnectionError

router = APIRouter(prefix="/api/admin/worker-capacity", tags=["Worker Capacity"])


@router.get("/cloudflare", response_model=CloudflareCapacityStatus)
async def get_cloudflare_capacity(db: DbSession, user: CurrentActiveUser) -> CloudflareCapacityStatus:
    await authorize_operation(db, user, "GET /api/admin/worker-capacity/cloudflare", GLOBAL)
    return await describe_cloudflare_capacity(db)


@router.post("/cloudflare", response_model=CloudflareCapacityConnectAccepted, status_code=202)
async def connect_cloudflare_capacity(
    request: CloudflareCapacityConnectRequest, db: DbSession, user: CurrentActiveUser,
) -> CloudflareCapacityConnectAccepted:
    await authorize_operation(db, user, "POST /api/admin/worker-capacity/cloudflare", GLOBAL)
    try:
        accepted, job = await enqueue_cloudflare_setup(db, request, user=user, settings=get_settings())
    except CapacityConnectionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    await db.commit()
    await publish_platform_job_update(job)
    return accepted


@router.put("/cloudflare/rules", response_model=CloudflareCapacityStatus)
async def put_cloudflare_capacity_rules(
    request: CloudflareCapacityRules, db: DbSession, user: CurrentActiveUser,
) -> CloudflareCapacityStatus:
    await authorize_operation(db, user, "PUT /api/admin/worker-capacity/cloudflare/rules", GLOBAL)
    try:
        result = await update_cloudflare_rules(db, request)
    except CapacityConnectionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    await db.commit()
    from src.services.work_dispatch import WAKE_PLATFORM_JOB, wake_pool_workers

    await wake_pool_workers(WAKE_PLATFORM_JOB)
    return result
