"""Contracts for Chat attachment and artifact retention settings."""

from pydantic import BaseModel, Field


class ArtifactRetentionSettings(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Whether scheduled cleanup deletes expired Chat attachments and artifacts.",
    )
    retention_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description="Number of days to retain Chat attachments and generated artifacts.",
    )


class ArtifactRetentionSettingsUpdate(BaseModel):
    enabled: bool
    retention_days: int = Field(ge=1, le=3650)
