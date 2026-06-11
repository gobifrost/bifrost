"""
Application contract models for Bifrost App Builder.

Provides Pydantic models for API request/response handling.
Applications use code-based files (TSX/TypeScript) stored in S3 via file_index.

Type Alignment:
These models are designed to match the frontend TypeScript types exactly.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
import re
from typing import Literal

EmbedHmacScheme = Literal["shopify", "halopsa"]


# ==================== APPLICATION MODELS ====================


class ApplicationBase(BaseModel):
    """Shared application fields."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Application display name",
    )
    description: str | None = Field(default=None, description="Optional application description")
    icon: str | None = Field(
        default=None,
        max_length=50,
        description="Icon identifier (e.g., 'home', 'settings', 'chart')",
    )


class ApplicationCreate(ApplicationBase):
    """Input for creating an application."""

    slug: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[a-z][a-z0-9-]*$",
        description="URL-friendly slug (lowercase letters, numbers, hyphens)",
    )
    access_level: str = Field(
        default="authenticated",
        description="Access level: 'authenticated' (any logged-in user) or 'role_based' (specific roles)",
    )
    app_model: str = Field(
        default="inline_v1",
        description="Render model: 'inline_v1' (legacy inline render) or 'standalone_v2' (own createRoot + router + real SDK)",
    )
    role_ids: list[UUID] = Field(
        default_factory=list,
        description="Role IDs for role_based access (ignored if access_level is 'authenticated')",
    )
    organization_id: UUID | None = Field(
        default=None,
        description="Organization ID. Null for global application.",
    )

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """Validate slug format."""
        if not re.match(r"^[a-z][a-z0-9-]*$", v):
            raise ValueError(
                "Slug must start with a letter and contain only lowercase letters, "
                "numbers, and hyphens"
            )
        return v

    @field_validator("access_level")
    @classmethod
    def validate_access_level(cls, v: str) -> str:
        """Validate access_level is one of the allowed values."""
        if v not in ("authenticated", "everyone", "role_based"):
            raise ValueError(
                "access_level must be 'authenticated', 'everyone', or 'role_based'"
            )
        return v


class ApplicationUpdate(BaseModel):
    """Input for updating application metadata."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z][a-z0-9-]*$",
        description="URL-friendly slug. Warning: changing this will change the app's URL.",
    )
    description: str | None = None
    icon: str | None = Field(default=None, max_length=50)
    scope: str | None = Field(
        default=None,
        description="Organization scope: 'global' for platform-wide, or org UUID string. Platform admin only.",
    )
    access_level: str | None = Field(
        default=None,
        description="Access level: 'authenticated' (any logged-in user) or 'role_based' (specific roles)",
    )
    role_ids: list[UUID] | None = Field(
        default=None,
        description="Role IDs for role_based access (replaces existing roles)",
    )
    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str | None) -> str | None:
        """Validate slug format."""
        if v is not None and not re.match(r"^[a-z][a-z0-9-]*$", v):
            raise ValueError(
                "Slug must start with a letter and contain only lowercase letters, "
                "numbers, and hyphens"
            )
        return v

    @field_validator("access_level")
    @classmethod
    def validate_access_level(cls, v: str | None) -> str | None:
        """Validate access_level is one of the allowed values."""
        if v is not None and v not in ("authenticated", "everyone", "role_based"):
            raise ValueError(
                "access_level must be 'authenticated', 'everyone', or 'role_based'"
            )
        return v


class ApplicationPublic(ApplicationBase):
    """Application output for API responses.

    This is the unified model for both list/get operations AND export/import.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    organization_id: UUID | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    is_published: bool
    has_unpublished_changes: bool
    access_level: str = Field(default="authenticated")
    app_model: str = Field(default="inline_v1", description="Render model: inline_v1 (legacy inline) | standalone_v2")
    is_solution_managed: bool = Field(default=False, description="True if managed by a deployed Solution (read-only on platform)")
    solution_id: UUID | None = Field(default=None, description="UUID of the owning Solution install (null if not solution-managed)")
    role_ids: list[UUID] = Field(default_factory=list)
    repo_path: str = Field(
        description="Workspace-relative path to the app's source directory. Mutated via POST /api/applications/{id}/replace."
    )
    logo: str | None = Field(
        default=None,
        description="Inline logo as a data URL, or null when no logo is set. Avoids an N+1 GET per card in list views.",
    )

    @field_serializer("created_at", "updated_at", "published_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class ApplicationListResponse(BaseModel):
    """Response for listing applications."""

    applications: list[ApplicationPublic]
    total: int


# ==================== DEFINITION MODELS ====================


class ApplicationDefinition(BaseModel):
    """Application definition (the complete app structure)."""

    model_config = ConfigDict(from_attributes=True)

    definition: dict[str, Any] | None = Field(
        default=None,
        description="Complete application definition (pages, components, etc.)",
    )
    version: int = Field(description="Version number of this definition")
    is_live: bool = Field(description="Whether this is the live or draft version")


class ApplicationDraftSave(BaseModel):
    """Input for saving a draft definition."""

    definition: dict[str, Any] = Field(
        ...,
        description="Complete application definition to save as draft",
    )


class ApplicationPublishRequest(BaseModel):
    """Request to publish draft to live."""

    message: str | None = Field(
        default=None,
        max_length=500,
        description="Optional publish message for version history",
    )


class ApplicationRollbackRequest(BaseModel):
    """Request to rollback to a previous version."""

    version_id: UUID = Field(
        ...,
        description="UUID of the version to rollback to",
    )


# ==================== VERSION HISTORY MODELS ====================


class VersionHistoryEntry(BaseModel):
    """A single entry in the version history."""

    version: int
    definition: dict[str, Any]
    published_at: datetime
    published_by: str | None
    message: str | None

    @field_serializer("published_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


class VersionHistoryResponse(BaseModel):
    """Response for version history endpoint."""

    history: list[VersionHistoryEntry]


# ==================== APP FILE MODELS ====================


class AppFileBase(BaseModel):
    """Shared code file fields."""

    path: str = Field(
        min_length=1,
        max_length=500,
        description="File path within the app (e.g., 'pages/clients/[id]', 'components/Button')",
    )


class AppFileCreate(AppFileBase):
    """Input for creating a code file."""

    source: str = Field(
        default="",
        description="Original source code",
    )


class AppFileUpdate(BaseModel):
    """Input for updating a code file."""

    source: str | None = Field(
        default=None,
        description="Updated source code",
    )
    compiled: str | None = Field(
        default=None,
        description="Compiled output",
    )


class AppFileResponse(AppFileBase):
    """Full code file response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    app_version_id: UUID = Field(description="ID of the version this file belongs to")
    source: str = Field(description="Original source code")
    compiled: str | None = Field(default=None, description="Compiled output")
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


class AppFileListResponse(BaseModel):
    """Response for listing code files."""

    files: list[AppFileResponse]
    total: int


# ==================== SIMPLE FILE MODELS (S3-backed) ====================


class SimpleFileResponse(BaseModel):
    """Single file response for S3-backed app files."""

    path: str = Field(description="Relative file path within the app (e.g., 'pages/index.tsx')")
    source: str = Field(description="File source content")
    compiled: str | None = Field(default=None, description="Pre-compiled JavaScript output")


class SimpleFileListResponse(BaseModel):
    """Response for listing S3-backed app files."""

    files: list[SimpleFileResponse]
    total: int


class RenderFileResponse(BaseModel):
    """Single compiled file for rendering (no source)."""

    path: str = Field(description="Relative file path within the app")
    code: str = Field(description="Compiled JavaScript ready for execution")


class AppRenderResponse(BaseModel):
    """All compiled files needed to render an application."""

    files: list[RenderFileResponse]
    total: int
    dependencies: dict[str, str] = Field(
        default_factory=dict,
        description="npm dependencies {name: version} for esm.sh loading",
    )
    styles: dict[str, str] = Field(
        default_factory=dict,
        description="CSS files {path: content} for style injection",
    )


# ==================== EMBED SECRET MODELS ====================


class EmbedSecretCreate(BaseModel):
    """Request to create an embed secret for an app."""

    name: str = Field(..., max_length=255, description="Label for this secret (e.g., 'Halo Production')")
    secret: str | None = Field(default=None, description="Shared secret. If omitted, one is auto-generated.")
    hmac_scheme: EmbedHmacScheme = Field(
        default="shopify",
        description="HMAC signing scheme. 'shopify' signs all query params; 'halopsa' signs only agent_id.",
    )


class EmbedSecretResponse(BaseModel):
    """Embed secret metadata (never includes the raw secret after creation)."""

    id: str
    name: str
    is_active: bool
    hmac_scheme: EmbedHmacScheme
    created_at: datetime

    @field_serializer("created_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


class EmbedSecretCreatedResponse(EmbedSecretResponse):
    """Response when creating an embed secret — includes raw secret shown once."""

    raw_secret: str


class EmbedSecretUpdate(BaseModel):
    """Request to update an embed secret."""

    is_active: bool | None = None
    name: str | None = Field(default=None, max_length=255)
    hmac_scheme: EmbedHmacScheme | None = None


# ==================== IMPORT MODELS ====================
# Applications use file sync (like forms/agents), not a dedicated import endpoint


class ApplicationReplaceRequest(BaseModel):
    """Input for repointing an application's source directory.

    Mutation-only surface. See ``POST /api/applications/{id}/replace``.
    """

    repo_path: str = Field(
        min_length=1,
        max_length=500,
        description="Workspace-relative path to the new source directory (e.g. apps/my-app-v2).",
    )
    force: bool = Field(
        default=False,
        description=(
            "Bypass the uniqueness, nesting, and source-exists checks. "
            "Use when repointing before files are pushed."
        ),
    )
