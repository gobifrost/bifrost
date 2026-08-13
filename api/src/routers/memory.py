"""Authenticated private-memory settings and entry endpoints."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.core.db_deps import DbSession
from src.models.contracts.memory import (
    MemoryDeleteResponse,
    MemoryEntryList,
    MemoryEntryPublic,
    MemoryPlatformSettings,
    MemoryPlatformSettingsUpdate,
    MemorySaveRequest,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResult,
    MemoryUserSettings,
    MemoryUserSettingsUpdate,
)
from src.services.memory import (
    MemoryConfigurationError,
    MemoryDisabledError,
    MemoryService,
)

router = APIRouter(prefix="/api/memory", tags=["Memory"])
admin_router = APIRouter(
    prefix="/api/admin/memory",
    tags=["Memory"],
    dependencies=[RequirePlatformAdmin],
)


def _service(db: DbSession, user: CurrentActiveUser) -> MemoryService:
    return MemoryService(
        db,
        user_id=user.user_id,
        organization_id=user.organization_id,
    )


def _entry_public(entry) -> MemoryEntryPublic:
    return MemoryEntryPublic(
        id=entry.id,
        content=entry.content,
        metadata=entry.doc_metadata,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _raise_memory_error(exc: Exception) -> None:
    if isinstance(exc, MemoryDisabledError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, MemoryConfigurationError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    raise exc


@admin_router.get("/settings", response_model=MemoryPlatformSettings)
async def get_platform_memory_settings(
    db: DbSession,
    current_user: CurrentActiveUser,
) -> MemoryPlatformSettings:
    return MemoryPlatformSettings(enabled=await _service(db, current_user).platform_enabled())


@admin_router.put("/settings", response_model=MemoryPlatformSettings)
async def update_platform_memory_settings(
    request: MemoryPlatformSettingsUpdate,
    db: DbSession,
    current_user: CurrentActiveUser,
) -> MemoryPlatformSettings:
    service = _service(db, current_user)
    await service.set_platform_enabled(request.enabled, updated_by=current_user.email)
    await db.commit()
    return MemoryPlatformSettings(enabled=request.enabled)


@router.get("/settings", response_model=MemoryUserSettings)
async def get_user_memory_settings(
    db: DbSession,
    current_user: CurrentActiveUser,
) -> MemoryUserSettings:
    return MemoryUserSettings(**await _service(db, current_user).settings())


@router.put("/settings", response_model=MemoryUserSettings)
async def update_user_memory_settings(
    request: MemoryUserSettingsUpdate,
    db: DbSession,
    current_user: CurrentActiveUser,
) -> MemoryUserSettings:
    service = _service(db, current_user)
    try:
        await service.set_user_enabled(request.enabled)
    except Exception as exc:
        _raise_memory_error(exc)
    await db.commit()
    return MemoryUserSettings(**await service.settings())


@router.get("", response_model=MemoryEntryList)
async def list_memories(
    db: DbSession,
    current_user: CurrentActiveUser,
) -> MemoryEntryList:
    entries = await _service(db, current_user).list_entries()
    return MemoryEntryList(entries=[_entry_public(entry) for entry in entries], count=len(entries))


@router.post("", response_model=MemoryEntryPublic, status_code=status.HTTP_201_CREATED)
async def save_memory(
    request: MemorySaveRequest,
    db: DbSession,
    current_user: CurrentActiveUser,
) -> MemoryEntryPublic:
    try:
        entry = await _service(db, current_user).save(request.content, request.metadata)
    except Exception as exc:
        _raise_memory_error(exc)
    await db.commit()
    await db.refresh(entry)
    return _entry_public(entry)


@router.post("/search", response_model=MemorySearchResponse)
async def search_memory(
    request: MemorySearchRequest,
    db: DbSession,
    current_user: CurrentActiveUser,
) -> MemorySearchResponse:
    try:
        matches = await _service(db, current_user).search(request.query, request.limit)
    except Exception as exc:
        _raise_memory_error(exc)
    results = [
        MemorySearchResult(**_entry_public(entry).model_dump(), score=score)
        for entry, score in matches
    ]
    return MemorySearchResponse(results=results, count=len(results))


@router.delete("/{memory_id}", response_model=MemoryDeleteResponse)
async def remove_memory(
    memory_id: UUID,
    db: DbSession,
    current_user: CurrentActiveUser,
) -> MemoryDeleteResponse:
    removed = await _service(db, current_user).remove(memory_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    await db.commit()
    return MemoryDeleteResponse(removed_id=memory_id)
