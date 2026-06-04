"""Invite request/response contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# Status string constants exposed via UserPublic.invite_status.
class InviteStatus:
    ACTIVE = "active"  # user.is_registered=True
    PENDING = "pending"  # invite exists, not consumed/expired
    EXPIRED = "expired"  # invite exists, past expires_at
    NEVER_INVITED = "never_invited"  # is_registered=False, no invite row


class UserInvitePublic(BaseModel):
    """Invite metadata returned to admins. Never includes the raw token after creation."""

    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class CreateInviteResponse(BaseModel):
    """Returned only at creation/regeneration — contains the raw registration link."""

    user_id: UUID
    expires_at: datetime
    registration_url: str  # full URL with raw token, e.g. https://app/accept-invite?token=...
    event_emitted: bool
    event_id: UUID | None = None


class SendInviteRequest(BaseModel):
    """Request to emit invite automation for an existing raw registration link."""

    registration_url: str


class RegisterFromInviteRequest(BaseModel):
    """Invitee submits this to consume the token and set up auth."""

    token: str
    name: str | None = None
    password: str | None = None  # optional; passkey path is also supported
