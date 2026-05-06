"""
Notification contract models for Bifrost.

Provides a unified notification model for real-time updates via WebSocket.
Supports:
- Progress notifications (GitHub setup, file uploads, package installs)
- Status notifications (success, error, warning, info)
- Scoped delivery (individual user, platform admins)
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NotificationCategory(str, Enum):
    """Categories for grouping notifications."""

    GITHUB_SETUP = "github_setup"  # Initial GitHub configuration
    GITHUB_SYNC = "github_sync"  # Pull/push operations
    FILE_UPLOAD = "file_upload"  # File uploads from editor
    PACKAGE_INSTALL = "package_install"  # Package installations
    EMBEDDING_REINDEX = "embedding_reindex"  # Re-embed knowledge_store rows after model swap
    SYSTEM = "system"  # System-level notifications


class NotificationStatus(str, Enum):
    """Status of a notification/operation."""

    PENDING = "pending"  # Queued, not yet started
    RUNNING = "running"  # In progress
    AWAITING_ACTION = "awaiting_action"  # Waiting for user action (no spinner)
    COMPLETED = "completed"  # Successfully completed
    FAILED = "failed"  # Failed with error
    CANCELLED = "cancelled"  # User cancelled


# =============================================================================
# Request/Response Models
# =============================================================================


class NotificationCreate(BaseModel):
    """Request to create a notification."""

    category: NotificationCategory = Field(..., description="Notification category")
    title: str = Field(
        ..., min_length=1, max_length=200, description="Short notification title"
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="Current status message (e.g., 'Cloning repository...')",
    )
    percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Optional progress percentage (0-100). None = indeterminate.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Additional metadata"
    )

    model_config = ConfigDict(from_attributes=True)


class NotificationUpdate(BaseModel):
    """Request to update a notification."""

    status: NotificationStatus | None = Field(default=None, description="New status")
    description: str | None = Field(
        default=None, max_length=500, description="Update status message"
    )
    percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Update progress percentage (0-100)",
    )
    error: str | None = Field(
        default=None, max_length=1000, description="Error message if failed"
    )
    result: dict[str, Any] | None = Field(
        default=None, description="Result data on completion"
    )

    model_config = ConfigDict(from_attributes=True)


class NotificationPublic(BaseModel):
    """Public representation of a notification."""

    id: str = Field(..., description="Unique notification ID")
    category: NotificationCategory = Field(..., description="Notification category")
    title: str = Field(..., description="Notification title")
    description: str | None = Field(default=None, description="Current status message")
    status: NotificationStatus = Field(..., description="Current status")
    percent: float | None = Field(
        default=None,
        description="Progress percentage. None = indeterminate, 0-100 = determinate.",
    )
    error: str | None = Field(default=None, description="Error message if failed")
    result: dict[str, Any] | None = Field(
        default=None, description="Result data on completion"
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Additional metadata"
    )
    created_at: datetime = Field(..., description="When notification was created")
    updated_at: datetime = Field(..., description="When notification was last updated")
    user_id: str = Field(..., description="User who owns this notification")

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# API Response Models
# =============================================================================


class NotificationListResponse(BaseModel):
    """Response containing list of notifications."""

    notifications: list[NotificationPublic] = Field(
        default_factory=list, description="Active notifications"
    )

    model_config = ConfigDict(from_attributes=True)


class JobDispatchResponse(BaseModel):
    """Response when dispatching a background job with notification tracking."""

    job_id: str = Field(..., description="Job ID for tracking")
    notification_id: str = Field(..., description="Notification ID for watching progress")
    status: str = Field(default="queued", description="Initial job status")

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Lock Models
# =============================================================================


class UploadLockInfo(BaseModel):
    """Information about an active upload lock."""

    locked: bool = Field(..., description="Whether upload is currently locked")
    owner_user_id: str | None = Field(
        default=None, description="User ID holding the lock"
    )
    owner_email: str | None = Field(
        default=None, description="Email of user holding the lock"
    )
    operation: str | None = Field(
        default=None, description="Description of the operation"
    )
    locked_at: datetime | None = Field(
        default=None, description="When lock was acquired"
    )
    expires_at: datetime | None = Field(default=None, description="When lock expires")

    model_config = ConfigDict(from_attributes=True)
