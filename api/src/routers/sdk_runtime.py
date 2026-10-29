"""Runtime-owned logging and service-control transports."""

from uuid import UUID

from fastapi import APIRouter, Query

from bifrost.models import ExecutionLog
from shared.models import (
    ProviderCacheReadRequest,
    ProviderCacheReadResponse,
    ProviderCacheWriteRequest,
    ProviderCacheWriteResponse,
    RuntimeLogRequest,
    RuntimeLogResponse,
    ServiceControlRequest,
    ServiceControlResponse,
)
from shared.sdk_provider_cache import read_provider_cache, write_provider_cache
from shared.runtime_io import read_runtime_logs, service_control, write_runtime_log
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession

router = APIRouter(prefix="/api/sdk/runtime", tags=["SDK Internals"])


@router.post("/logs", response_model=RuntimeLogResponse)
async def append_runtime_log(body: RuntimeLogRequest, user: CurrentActiveUser) -> RuntimeLogResponse:
    return await write_runtime_log(user, body)


@router.post("/service-control", response_model=ServiceControlResponse)
async def get_service_control(body: ServiceControlRequest, user: CurrentActiveUser) -> ServiceControlResponse:
    return await service_control(user, body)


@router.post("/data-provider-cache/read", response_model=ProviderCacheReadResponse)
async def read_runtime_data_provider_cache(
    body: ProviderCacheReadRequest, user: CurrentActiveUser, db: DbSession,
) -> ProviderCacheReadResponse:
    return await read_provider_cache(db, user, body)


@router.post("/data-provider-cache/write", response_model=ProviderCacheWriteResponse)
async def write_runtime_data_provider_cache(
    body: ProviderCacheWriteRequest, user: CurrentActiveUser, db: DbSession,
) -> ProviderCacheWriteResponse:
    return await write_provider_cache(db, user, body)


@router.get("/logs/{execution_id}", response_model=list[ExecutionLog])
async def get_runtime_logs(
    execution_id: UUID, user: CurrentActiveUser, db: DbSession,
    start: str = Query("0", pattern=r"^(?:0|\d+-\d+)$"), count: int = Query(100, ge=1, le=10000),
) -> list[ExecutionLog]:
    return await read_runtime_logs(db, user, execution_id, start=start, count=count)
