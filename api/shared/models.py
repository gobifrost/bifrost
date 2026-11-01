"""Small shared request and response models used outside a single API domain."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


SEMVER_PATTERN = (
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*))"
    r"(?:\.(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*)))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class UsageReport(BaseModel):
    """Pseudonymous aggregate activity for one completed UTC day."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    installation_id: UUID
    version: str = Field(min_length=5, max_length=128, pattern=SEMVER_PATTERN)
    period: date
    workflow_runs: int = Field(ge=0, le=1_000_000_000)
    agent_runs: int = Field(ge=0, le=1_000_000_000)
    environment: Literal["production"] = "production"

    @field_validator("installation_id")
    @classmethod
    def require_v4_installation_id(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("installation_id must be a UUIDv4")
        return value


class BrowserImpersonationTokenResponse(BaseModel):
    """A short-lived bearer credential for one browser impersonation tab."""
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: Literal[1800] = 1800


class SolutionDevContextRequest(BaseModel):
    """The selected portable app identity for a local Solution preview."""

    app_id: UUID


class SolutionDevContextResponse(BaseModel):
    """Short-lived, app-bound context accepted only as a supplemental header."""

    token: str
    expires_at: float
    solution_id: UUID
    organization_id: UUID | None
    app_id: UUID


class RuntimeLogRequest(BaseModel):
    """A line for the authenticated runtime's own log stream."""

    model_config = ConfigDict(extra="forbid")
    level: str = Field(min_length=1, max_length=32)
    message: str = Field(max_length=65536)
    metadata: dict[str, Any] | None = None
    timestamp: datetime | None = None


class RuntimeLogResponse(BaseModel):
    entry_id: str | None


class ServiceControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ready: bool = False
    token_expires_at: AwareDatetime


class ServiceControlResponse(BaseModel):
    stop_requested: bool
    access_token: str | None = None
    token_expires_at: datetime | None = None


class ProviderCacheReadRequest(BaseModel):
    """Parameters for the authenticated runtime's own data-provider cache read."""

    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProviderCacheReadResponse(BaseModel):
    hit: bool
    result: Any | None = None
    expires_at: datetime | None = None


class ProviderCacheWriteRequest(BaseModel):
    """Result for the authenticated runtime's own data-provider cache entry."""

    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: Any


class ProviderCacheWriteResponse(BaseModel):
    expires_at: datetime | None = None


class DeviceAuthorizeRequest(BaseModel):
    """The code for a CLI device login being approved or denied."""

    user_code: str


class AppSdkUpgradeSettings(BaseModel):
    """Platform-wide policy for automatic App SDK upgrades."""

    enabled: bool = False


class AppSdkUpgradeSettingsUpdate(BaseModel):
    """Requested platform-wide App SDK upgrade policy."""

    enabled: bool


class ApplicationSdkUpdatePayloadV1(BaseModel):
    """Legacy manual App SDK update payload retained for already queued jobs."""

    model_config = ConfigDict(extra="forbid")

    application_id: UUID
    deployment_id: UUID = Field(default_factory=uuid4)
    expected_active_deployment_id: UUID | None = None
    expected_sdk_package_version: str | None = None
    expected_sdk_fingerprint: str | None = None
    expected_sdk_contract_version: int | None = None
    expected_sdk_built_at: datetime | None = None


class HostedAppSdkDescriptor(BaseModel):
    """The current immutable browser SDK factory for one binding API major."""

    api_major: int = Field(ge=1)
    factory_version: int = Field(ge=1)
    factory_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    integrity: str = Field(pattern=r"^sha256-[A-Za-z0-9+/]{43}=$")
    asset_url: str = Field(pattern=r"^/api/app-sdk/assets/[a-f0-9]{64}\.js$")


class ApplicationSdkUpdatePayloadV2(ApplicationSdkUpdatePayloadV1):
    """Pinned-target jobs queued before hosted binding provenance existed."""

    target_sdk_fingerprint: str = Field(min_length=1)
    target_sdk_contract_version: int = Field(ge=1)
    automatic_upgrade: bool = False


class ApplicationSdkUpdatePayload(ApplicationSdkUpdatePayloadV2):
    """Version 3 fences the binding API major as well as the compiled bytes."""

    expected_sdk_binding_api_major: int | None = Field(default=None, ge=1)
    target_sdk_binding_api_major: int | None = Field(default=None, ge=1)


class VersionResponse(BaseModel):
    version: str
    min_cli_version: str
    contract_version: int
    sdk_fingerprint: str
    sdk_contract_version: int
    sdk_binding_api_major: int | None = None
    sdk_supported_binding_api_majors: list[int] = Field(default_factory=list)


class ApplicationBundleManifest(BaseModel):
    """Built App assets and the host contracts needed before entry evaluation."""

    entry: str | None
    css: str | None
    base_url: str
    mode: Literal["preview", "live"]
    dependencies: dict[str, str]
    migrated: bool = False
    organization_id: UUID | None = None
    app_model: Literal["inline_v1", "standalone_v2"] = "inline_v1"
    runtime_contract: Literal["mount-v1"] | None = None
    sdk_binding_api_major: int | None = Field(default=None, ge=1)
