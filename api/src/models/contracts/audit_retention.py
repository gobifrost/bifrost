"""Contracts for audit event retention settings."""

from datetime import date, datetime, timedelta
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from src.models.contracts.platform_jobs import PlatformJobPublic


def _check_windows(hot_days: int, archive_days: int | None) -> None:
    if archive_days is not None and archive_days < hot_days:
        raise ValueError(
            "archive_days must be at least hot_days, or null to keep archives forever"
        )


class AuditRetentionSettings(BaseModel):
    hot_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description="Days events stay in the database before they are archived.",
    )
    archive_days: int | None = Field(
        default=365,
        ge=1,
        description="Age in days at which archived events are deleted; null keeps them forever.",
    )

    @model_validator(mode="after")
    def _archive_not_shorter(self) -> "AuditRetentionSettings":
        _check_windows(self.hot_days, self.archive_days)
        return self


class AuditRetentionSettingsUpdate(BaseModel):
    hot_days: int = Field(ge=1, le=3650)
    archive_days: int | None = Field(ge=1)

    @model_validator(mode="after")
    def _archive_not_shorter(self) -> "AuditRetentionSettingsUpdate":
        _check_windows(self.hot_days, self.archive_days)
        return self


class AuditRetentionInfo(BaseModel):
    hot_days: int
    archive_days: int | None
    oldest_in_database: datetime | None
    archived_through: datetime | None
    archived_segments: int
    archived_rows: int


class AuditRetentionStatus(BaseModel):
    settings: AuditRetentionSettings
    info: AuditRetentionInfo
    last_run: PlatformJobPublic | None


class AuditArchiveRunRequest(BaseModel):
    dry_run: bool = False


class AuditExpiryPreview(BaseModel):
    expiring_segments: int
    expiring_rows: int
    expiring_from: date | None
    expiring_to: date | None


class AuditExportRequest(BaseModel):
    start_date: AwareDatetime = Field(description="Start of the export range (inclusive).")
    end_date: AwareDatetime = Field(description="End of the export range (inclusive).")
    organization_id: UUID | None = Field(
        default=None, description="Export only this organization's events; omit for every organization you reach."
    )
    action: str | None = Field(
        default=None, description="Action prefix filter, e.g. 'access.check'."
    )

    @model_validator(mode="after")
    def _bounded_range(self) -> "AuditExportRequest":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if self.end_date - self.start_date > timedelta(days=366):
            raise ValueError("an export covers at most 366 days")
        return self
