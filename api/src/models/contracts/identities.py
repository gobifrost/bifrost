"""Contracts for identities: the accounts that run work no person started."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator

from src.models.contracts.role_assignments import RoleBoundaryPublic

IdentityName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
FederatedCredentialName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]

SUBJECT_TOKEN_ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"
SUBJECT_TOKEN_ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
SUPPORTED_SUBJECT_TOKEN_TYPES = frozenset({SUBJECT_TOKEN_ID_TOKEN, SUBJECT_TOKEN_ACCESS_TOKEN})


class IdentityBaseRole(BaseModel):
    id: UUID
    name: str


class IdentityRole(BaseModel):
    """An additional role the identity holds, and where it applies."""

    role_id: UUID
    name: str
    boundaries: list[RoleBoundaryPublic]


class IdentityPublic(BaseModel):
    id: UUID
    name: str
    identity_kind: Literal["org_default", "global_default", "custom"]
    organization_id: UUID | None = Field(description="Null for Global identities.")
    organization_name: str | None
    base_role: IdentityBaseRole
    additional_roles: list[IdentityRole]
    workflows_using: int = Field(
        description=(
            "Workflows that run unattended as this identity. A default identity also runs the "
            "workflows of its organization (Global: of no organization) that name no identity."
        )
    )


class IdentityCreate(BaseModel):
    """A new custom identity with an explicit or configured default base role."""

    name: IdentityName
    organization_id: UUID | None = Field(description="The identity's organization; null for a Global identity.")
    base_role_id: UUID | None = Field(default=None, description="Explicit base role; omit to use the organization's or instance's default.")


class IdentityUpdate(BaseModel):
    name: IdentityName


class FederatedCredentialCreate(BaseModel):
    name: FederatedCredentialName
    issuer: str = Field(min_length=1, max_length=2048)
    discovery_url: str | None = Field(default=None, max_length=2048)
    audience: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=1024)
    claims: dict[str, str] = Field(default_factory=dict, max_length=32)
    subject_token_type: str = SUBJECT_TOKEN_ID_TOKEN
    token_ttl_seconds: int = Field(default=300, ge=60, le=3600)
    enabled: bool = True

    @field_validator("issuer")
    @classmethod
    def https_issuer(cls, value: str | None) -> str:
        from urllib.parse import urlparse

        if not isinstance(value, str):
            raise ValueError("issuer must not be null")
        normalized = value.strip()
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("issuer must be an HTTPS URL without credentials, query, or fragment")
        return normalized

    @field_validator("discovery_url")
    @classmethod
    def https_discovery_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return cls.https_issuer(value)

    @field_validator("audience", "subject")
    @classmethod
    def exact_nonempty_value(cls, value: str | None) -> str:
        if not isinstance(value, str):
            raise ValueError("value must not be null")
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("claims")
    @classmethod
    def scalar_claims(cls, value: dict[str, str] | None) -> dict[str, str]:
        if value is None:
            raise ValueError("claims must not be null")
        normalized: dict[str, str] = {}
        for key, claim_value in value.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(claim_value, str):
                raise ValueError("claims must map non-empty names to string values")
            normalized[key] = claim_value
        return normalized

    @field_validator("subject_token_type")
    @classmethod
    def supported_subject_token_type(cls, value: str | None) -> str:
        if value not in SUPPORTED_SUBJECT_TOKEN_TYPES:
            raise ValueError("subject_token_type must be an RFC ID-token or access-token type")
        return value


class FederatedCredentialUpdate(BaseModel):
    name: FederatedCredentialName | None = None
    issuer: str | None = Field(default=None, min_length=1, max_length=2048)
    discovery_url: str | None = Field(default=None, max_length=2048)
    audience: str | None = Field(default=None, min_length=1, max_length=512)
    subject: str | None = Field(default=None, min_length=1, max_length=1024)
    claims: dict[str, str] | None = Field(default=None, max_length=32)
    subject_token_type: str | None = None
    token_ttl_seconds: int | None = Field(default=None, ge=60, le=3600)
    enabled: bool | None = None

    @field_validator("name", "token_ttl_seconds", "enabled", mode="before")
    @classmethod
    def non_null_update(cls, value: object) -> object:
        if value is None:
            raise ValueError("value must not be null")
        return value

    _https_issuer = field_validator("issuer")(FederatedCredentialCreate.https_issuer)
    _https_discovery_url = field_validator("discovery_url")(FederatedCredentialCreate.https_discovery_url)
    _exact_nonempty_value = field_validator("audience", "subject")(FederatedCredentialCreate.exact_nonempty_value)
    _scalar_claims = field_validator("claims")(FederatedCredentialCreate.scalar_claims)
    _supported_subject_token_type = field_validator("subject_token_type")(FederatedCredentialCreate.supported_subject_token_type)


class FederatedCredentialPublic(FederatedCredentialCreate):
    id: UUID
    identity_id: UUID
