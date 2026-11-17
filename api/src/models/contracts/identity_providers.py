"""Request/response contracts for identity provider connections and
organization domain claims."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

IdentityProviderVendor = Literal["entra", "okta", "jumpcloud", "google", "other"]
# google_directory joins once the Directory sync lands; v1 creates SCIM only.
IdentityProviderSourceType = Literal["scim"]
TokenLifetimeDays = Literal[90, 180, 365, 730]


class OrganizationDomainCreate(BaseModel):
    organization_id: UUID
    domain: str = Field(min_length=1, max_length=253)


class OrganizationDomainResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    domain: str
    verified_at: datetime | None
    last_checked_at: datetime | None
    # Publish a TXT record named ``txt_record_name`` with this exact value.
    txt_record_name: str
    txt_record_value: str
    created_at: datetime


class IdentitySource(BaseModel):
    """Where a user's identity comes from (None on a user means Native)."""

    identity_provider_id: UUID
    name: str
    vendor: IdentityProviderVendor
    # Set while an activation, role grant or rename from the identity
    # provider waits for the access pass (applied within about two minutes).
    access_pending_since: datetime | None = None


class IdentityProviderTokenInfo(BaseModel):
    hint: str
    expires_at: datetime
    created_at: datetime
    last_used_at: datetime | None


class IdentityProviderCreate(BaseModel):
    organization_id: UUID
    name: str = Field(min_length=1, max_length=255)
    vendor: IdentityProviderVendor
    source_type: IdentityProviderSourceType = "scim"
    provision_as_external: bool = False
    token_lifetime_days: TokenLifetimeDays = 365


class IdentityProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    vendor: IdentityProviderVendor | None = None
    is_enabled: bool | None = None
    provision_as_external: bool | None = None


class IdentityProviderResponse(BaseModel):
    id: UUID
    organization_id: UUID
    organization_name: str
    name: str
    source_type: str
    vendor: IdentityProviderVendor
    is_enabled: bool
    provision_as_external: bool
    # The SCIM "Tenant URL" to configure in the identity provider.
    scim_base_url: str
    token: IdentityProviderTokenInfo | None
    verified_domains: list[str]
    user_count: int
    group_count: int
    mapped_group_count: int
    # Users whose activation, role grant or rename waits for the access pass.
    pending_access_count: int
    last_request_at: datetime | None
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IdentityProviderTokenRotate(BaseModel):
    lifetime_days: TokenLifetimeDays = 365


class IdentityProviderTokenIssued(BaseModel):
    """The raw token is returned only here, once."""

    token: str
    info: IdentityProviderTokenInfo
    # Earlier tokens stop working at this time (the rotation overlap).
    previous_tokens_expire_at: datetime | None


class IdentityProviderCreated(BaseModel):
    identity_provider: IdentityProviderResponse
    token: IdentityProviderTokenIssued


class MappedRole(BaseModel):
    id: UUID
    name: str


class IdentityProviderGroupResponse(BaseModel):
    id: UUID
    display_name: str
    external_id: str | None
    member_count: int
    mapped: bool
    roles: list[MappedRole]


class IdentityProviderGroupMappingUpdate(BaseModel):
    """Map a group. An empty ``role_ids`` maps it for access only."""

    role_ids: list[UUID] = Field(default_factory=list, max_length=50)
