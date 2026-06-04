"""
Passkey/WebAuthn contract models.

API request and response models for passkey (WebAuthn) operations.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# =============================================================================
# Registration
# =============================================================================


class PasskeyRegistrationOptionsRequest(BaseModel):
    """Request to generate passkey registration options."""

    device_name: str | None = Field(
        default=None,
        description="Optional friendly name for the passkey (e.g., 'MacBook Pro Touch ID')",
        max_length=255,
    )


class PasskeyRegistrationOptionsResponse(BaseModel):
    """Response with WebAuthn registration options for the browser."""

    options: dict[str, Any] = Field(
        description="WebAuthn registration options JSON for navigator.credentials.create()"
    )


class PasskeyRegistrationVerifyRequest(BaseModel):
    """Request to verify passkey registration."""

    credential: dict[str, Any] = Field(
        description="WebAuthn registration credential JSON from navigator.credentials.create()"
    )
    device_name: str | None = Field(
        default=None,
        description="Optional friendly name for the passkey",
        max_length=255,
    )


class PasskeyRegistrationVerifyResponse(BaseModel):
    """Response after successful passkey registration."""

    verified: bool = Field(description="Whether registration was successful")
    passkey_id: UUID = Field(description="ID of the newly created passkey")
    name: str = Field(description="Name of the passkey")


# =============================================================================
# Authentication
# =============================================================================


class PasskeyAuthOptionsRequest(BaseModel):
    """Request to generate passkey authentication options."""

    email: str | None = Field(
        default=None,
        description="Optional email to target specific user's credentials. "
        "If None, uses discoverable credentials (passkey autofill).",
    )


class PasskeyAuthOptionsResponse(BaseModel):
    """Response with WebAuthn authentication options for the browser."""

    challenge_id: str = Field(
        description="Challenge ID to include in the verify request"
    )
    options: dict[str, Any] = Field(
        description="WebAuthn authentication options JSON for navigator.credentials.get()"
    )


class PasskeyAuthVerifyRequest(BaseModel):
    """Request to verify passkey authentication."""

    challenge_id: str = Field(description="Challenge ID from the options response")
    credential: dict[str, Any] = Field(
        description="WebAuthn authentication credential JSON from navigator.credentials.get()"
    )


# =============================================================================
# Passkey Management
# =============================================================================


class PasskeyPublic(BaseModel):
    """Public representation of a user's passkey."""

    id: UUID = Field(description="Passkey ID")
    name: str = Field(description="User-friendly name for the passkey")
    device_type: str = Field(description="Device type: 'singleDevice' or 'multiDevice'")
    backed_up: bool = Field(
        description="Whether the passkey is synced to cloud (iCloud Keychain, Google Password Manager, etc.)"
    )
    created_at: datetime = Field(description="When the passkey was registered")
    last_used_at: datetime | None = Field(
        description="When the passkey was last used for authentication"
    )

    model_config = {"from_attributes": True}


class PasskeyListResponse(BaseModel):
    """Response with list of user's passkeys."""

    passkeys: list[PasskeyPublic] = Field(description="List of user's passkeys")
    count: int = Field(description="Total number of passkeys")


class PasskeyDeleteResponse(BaseModel):
    """Response after deleting a passkey."""

    deleted: bool = Field(description="Whether the passkey was deleted")
    passkey_id: UUID = Field(description="ID of the deleted passkey")


# =============================================================================
# First-Time Setup with Passkey (Passwordless Registration)
# =============================================================================


class SetupPasskeyOptionsRequest(BaseModel):
    """Request to start passwordless setup with passkey for first-time platform setup."""

    email: str = Field(
        description="Email address for the new admin account",
        max_length=255,
    )
    name: str | None = Field(
        default=None,
        description="Display name for the user",
        max_length=255,
    )


class SetupPasskeyOptionsResponse(BaseModel):
    """Response with registration token and WebAuthn options for first-time setup."""

    registration_token: str = Field(
        description="Token to complete registration (expires in 5 minutes)"
    )
    options: dict[str, Any] = Field(
        description="WebAuthn registration options JSON for navigator.credentials.create()"
    )
    expires_in: int = Field(
        default=300,
        description="Token expiry in seconds"
    )


class SetupPasskeyVerifyRequest(BaseModel):
    """Request to complete passwordless setup with passkey."""

    registration_token: str = Field(
        description="Token from the options response"
    )
    credential: dict[str, Any] = Field(
        description="WebAuthn credential from navigator.credentials.create()"
    )
    device_name: str | None = Field(
        default=None,
        description="Friendly name for the passkey",
        max_length=255,
    )


class SetupPasskeyVerifyResponse(BaseModel):
    """Response after successful passwordless setup with passkey."""

    user_id: str = Field(description="Created user ID")
    email: str = Field(description="User email")
    access_token: str = Field(description="JWT access token")
    refresh_token: str = Field(description="JWT refresh token")
    token_type: str = "bearer"


class InvitePasskeyOptionsRequest(BaseModel):
    """Request to start passkey registration from an invite token."""

    token: str = Field(description="Invite token from the registration URL")


class InvitePasskeyVerifyRequest(BaseModel):
    """Request to complete invite registration with a passkey."""

    token: str = Field(description="Invite token from the registration URL")
    credential: dict[str, Any] = Field(
        description="WebAuthn credential from navigator.credentials.create()"
    )
    device_name: str | None = Field(
        default=None,
        description="Friendly name for the passkey",
        max_length=255,
    )
