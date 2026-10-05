"""Contracts for run and event retention settings."""

from datetime import date, datetime

from pydantic import BaseModel, Field

from src.models.contracts.platform_jobs import PlatformJobPublic

_DAYS_DESCRIPTION = "Days a finished run or event is kept; null keeps them forever."


class RunRetentionSettings(BaseModel):
    days: int | None = Field(default=30, ge=30, le=3650, description=_DAYS_DESCRIPTION)


class RunRetentionSettingsUpdate(BaseModel):
    days: int | None = Field(..., ge=30, le=3650, description=_DAYS_DESCRIPTION)


class RunRetentionInfo(BaseModel):
    days: int | None
    oldest_finished_run: datetime | None
    rolled_up_runs: int
    rolled_up_through: date | None


class RunRetentionStatus(BaseModel):
    settings: RunRetentionSettings
    info: RunRetentionInfo
    last_run: PlatformJobPublic | None


class RunRetentionRunRequest(BaseModel):
    dry_run: bool = False


class RunRetentionPreview(BaseModel):
    days: int | None
    cutoff: datetime | None
    workflow_runs: int
    agent_runs: int
    events: int


class RunRetentionPublic(BaseModel):
    days: int | None
