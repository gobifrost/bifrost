"""API contracts for the immutable Product Updates runtime bundle."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductUpdateSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr: int | None = None
    commit: str | None = None
    role: str | None = None


class ProductUpdateContributor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str
    profile_url: str
    source_pr: int = Field(ge=1)
    role: str | None = None


class ProductUpdateAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    url: str
    alt: str
    caption: str | None = None


class ProductUpdateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    revision: int = Field(ge=1)
    published_at: str
    title: str = Field(min_length=1)
    markdown: str
    area: Literal[
        "Agents",
        "Apps & Forms",
        "Workflows",
        "Integrations",
        "Administration",
        "Platform",
        "Developer Tools",
    ]
    type: Literal["New", "Improved", "Fixed", "Security"]
    action_required: bool
    sources: list[ProductUpdateSource]
    contributors: list[ProductUpdateContributor]
    assets: list[ProductUpdateAsset]
    additional_areas: list[str] | None = None
    release: str | None = None
    in_app: bool | None = None


class ProductUpdateOtherChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    title: str
    url: str
    contributors: list[ProductUpdateContributor]
    category: Literal["fix", "hardening"] | None = None


class ProductUpdatesBundle(BaseModel):
    """The approved, build-pinned Product Updates bundle served at runtime."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    target_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    content_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    entries: list[ProductUpdateEntry]
    other_changes: list[ProductUpdateOtherChange]

    @field_validator("entries")
    @classmethod
    def require_unique_entry_ids(
        cls, entries: list[ProductUpdateEntry]
    ) -> list[ProductUpdateEntry]:
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("duplicate entry id in Product Updates bundle")
        return entries


class ProductUpdatesFeedResponse(BaseModel):
    bundle: ProductUpdatesBundle
    seen_entry_ids: list[str]


class ProductUpdatesReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_ids: list[UUID] = Field(min_length=1, max_length=1000)


class ProductUpdatesReceiptResponse(BaseModel):
    seen_entry_ids: list[str]
