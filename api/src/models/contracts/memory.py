"""Contracts for private Bifrost memory."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MemoryPlatformSettings(BaseModel):
    enabled: bool


class MemoryPlatformSettingsUpdate(BaseModel):
    enabled: bool


class MemoryUserSettings(BaseModel):
    platform_enabled: bool
    user_enabled: bool
    effective_enabled: bool


class MemoryUserSettingsUpdate(BaseModel):
    enabled: bool


class MemoryEntryPublic(BaseModel):
    id: UUID
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class MemoryEntryList(BaseModel):
    entries: list[MemoryEntryPublic]
    count: int


class MemorySaveRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=20)


class MemorySearchResult(MemoryEntryPublic):
    score: float


class MemorySearchResponse(BaseModel):
    results: list[MemorySearchResult]
    count: int


class MemoryDeleteResponse(BaseModel):
    removed_id: UUID
