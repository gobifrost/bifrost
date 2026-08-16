"""Stable contracts for generated files shared by Chat, workflows, and MCP."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ArtifactFormat = Literal["pdf", "docx", "xlsx", "csv", "html", "markdown", "text", "json"]


class ArtifactRef(BaseModel):
    """Opaque portable reference returned by Bifrost tools and accepted as input."""

    type: Literal["bifrost_artifact"] = "bifrost_artifact"
    id: str = Field(min_length=1)
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)


class ImageArtifactSpec(BaseModel):
    """Prompt for a provider-generated image saved as a Chat artifact."""

    filename: str = Field(
        min_length=1,
        max_length=200,
        description="A short, descriptive filename; Bifrost applies proper casing and the extension.",
    )
    prompt: str = Field(min_length=1, max_length=20_000)


class VideoArtifactSpec(BaseModel):
    """Prompt for a durable provider-generated video job."""

    filename: str = Field(
        min_length=1,
        max_length=200,
        description="A short, descriptive filename; Bifrost applies proper casing and the extension.",
    )
    prompt: str = Field(min_length=1, max_length=20_000)


class ArtifactTable(BaseModel):
    """A bounded table that can be rendered into a document."""

    columns: list[str] = Field(min_length=1, max_length=30)
    rows: list[list[Any]] = Field(default_factory=list, max_length=2_000)

    @model_validator(mode="after")
    def validate_row_widths(self) -> "ArtifactTable":
        expected = len(self.columns)
        if any(len(row) != expected for row in self.rows):
            raise ValueError("Every table row must have the same number of values as columns.")
        return self


class DocumentImage(BaseModel):
    """A raster file from the active artifact workspace."""

    path: str = Field(
        min_length=1,
        max_length=500,
        description="Logical workspace path or filename returned by an earlier artifact tool.",
    )
    caption: str | None = Field(default=None, max_length=500)
    max_width_inches: float = Field(default=6.5, ge=1, le=7)


class DocumentSection(BaseModel):
    """One flowing section in a PDF or DOCX artifact."""

    heading: str | None = Field(default=None, max_length=300)
    paragraphs: list[str] = Field(default_factory=list, max_length=100)
    bullets: list[str] = Field(default_factory=list, max_length=100)
    table: ArtifactTable | None = None
    images: list[DocumentImage] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def require_content(self) -> "DocumentSection":
        if not (
            self.heading
            or self.paragraphs
            or self.bullets
            or self.table
            or self.images
        ):
            raise ValueError(
                "A document section must contain a heading, text, bullets, a table, or an image."
            )
        return self


class DocumentArtifactSpec(BaseModel):
    """Schema-first payload for a flowing PDF or DOCX document."""

    filename: str = Field(
        min_length=1,
        max_length=200,
        description="A short, descriptive filename; Bifrost applies proper casing and the extension.",
    )
    format: Literal["pdf", "docx"]
    title: str = Field(min_length=1, max_length=300)
    subtitle: str | None = Field(default=None, max_length=500)
    sections: list[DocumentSection] = Field(min_length=1, max_length=80)
    page_size: Literal["letter", "a4"] = "letter"


class SpreadsheetSheetSpec(BaseModel):
    """One worksheet with a header row and tabular data."""

    name: str = Field(min_length=1, max_length=31)
    columns: list[str] = Field(min_length=1, max_length=100)
    rows: list[list[Any]] = Field(default_factory=list, max_length=20_000)
    freeze_header: bool = True
    auto_filter: bool = True

    @field_validator("name")
    @classmethod
    def validate_sheet_name(cls, value: str) -> str:
        if any(character in value for character in "[]:*?/\\"):
            raise ValueError("Worksheet names cannot contain []:*?/\\ characters.")
        return value

    @model_validator(mode="after")
    def validate_row_widths(self) -> "SpreadsheetSheetSpec":
        expected = len(self.columns)
        if any(len(row) != expected for row in self.rows):
            raise ValueError("Every worksheet row must have the same number of values as columns.")
        return self


class SpreadsheetArtifactSpec(BaseModel):
    """Schema-first payload for an XLSX workbook."""

    filename: str = Field(
        min_length=1,
        max_length=200,
        description="A short, descriptive filename; Bifrost applies proper casing and the extension.",
    )
    sheets: list[SpreadsheetSheetSpec] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_unique_sheet_names(self) -> "SpreadsheetArtifactSpec":
        names = [sheet.name.casefold() for sheet in self.sheets]
        if len(names) != len(set(names)):
            raise ValueError("Worksheet names must be unique.")
        return self


class TextArtifactSpec(BaseModel):
    """Schema-first payload for a text, HTML, CSV, Markdown, or JSON file."""

    filename: str = Field(
        min_length=1,
        max_length=200,
        description="A short, descriptive filename; Bifrost applies proper casing and the extension.",
    )
    format: Literal["csv", "html", "markdown", "text", "json"]
    content: str = Field(min_length=1, max_length=2_000_000)


class ModelCapabilities(BaseModel):
    """Persisted, fingerprinted model features used by Chat at runtime."""

    image_input: bool = False
    pdf_input: bool = False
    tool_calling: bool = False
    source: Literal["openrouter", "verified", "manual", "unknown"] = "unknown"
    checked_at: datetime | None = None
    fingerprint: str = ""


class ModelCapabilityLookupRequest(BaseModel):
    """Identify a configured model for deterministic catalog lookup."""

    provider: Literal["openai", "anthropic", "google"]
    model: str = Field(min_length=1)
    endpoint: str | None = None


class ModelCapabilityLookupResponse(BaseModel):
    """Capability lookup result plus an explanation suitable for settings UI."""

    capabilities: ModelCapabilities
    message: str


class ModelCapabilityVerifyRequest(ModelCapabilityLookupRequest):
    """Run a one-time conformance check against the configured provider."""

    api_key: str | None = Field(
        default=None,
        description="New unsaved API key; omit to use the saved provider key.",
    )


class ArtifactDownloadResponse(BaseModel):
    """Short-lived authenticated download location for an artifact."""

    url: str
