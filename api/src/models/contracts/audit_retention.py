"""Contracts for audit event retention settings."""

from pydantic import BaseModel, Field, model_validator


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
