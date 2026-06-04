"""
Application Configuration

Uses pydantic-settings for environment variable loading with validation.
All configuration is centralized here for easy management.
"""

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_temp_location() -> str:
    return str(Path(tempfile.gettempdir()) / "bifrost")


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Environment variables can be set directly or via .env file.
    All secrets should be provided via environment variables in production.
    """

    model_config = SettingsConfigDict(
        env_prefix="BIFROST_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==========================================================================
    # Environment
    # ==========================================================================
    environment: Literal["development", "testing", "production"] = Field(
        default="development", description="Runtime environment"
    )

    debug: bool = Field(default=False, description="Enable debug mode")

    # ==========================================================================
    # Database (PostgreSQL)
    # ==========================================================================
    database_url: str = Field(
        default="postgresql+asyncpg://bifrost:bifrost_dev@localhost:5432/bifrost",
        description="Async PostgreSQL connection URL",
    )

    database_url_sync: str = Field(
        default="postgresql://bifrost:bifrost_dev@localhost:5432/bifrost",
        description="Sync PostgreSQL connection URL (for Alembic)",
    )

    database_pool_size: int = Field(
        default=5, description="Database connection pool size"
    )

    database_max_overflow: int = Field(
        default=10, description="Max overflow connections beyond pool size"
    )

    # ==========================================================================
    # RabbitMQ
    # ==========================================================================
    rabbitmq_url: str = Field(
        default="amqp://bifrost:bifrost_dev@localhost:5672/",
        description="RabbitMQ connection URL",
    )

    # ==========================================================================
    # Workflow Execution
    # ==========================================================================
    max_concurrency: int = Field(
        default=10,
        description="Max concurrent workflow executions (controls RabbitMQ prefetch)",
    )

    # Process Pool Configuration (on-demand only — every execution forks
    # a fresh one-shot worker, capped at `max_workers` concurrent forks).
    max_workers: int = Field(
        default=10,
        description="Maximum concurrent worker processes (queue cap)"
    )
    execution_timeout_seconds: int = Field(
        default=300, description="Default execution timeout in seconds (5 minutes)"
    )
    graceful_shutdown_seconds: int = Field(
        default=5, description="Seconds to wait after SIGTERM before SIGKILL"
    )
    worker_heartbeat_interval_seconds: int = Field(
        default=10,
        description="Interval in seconds between worker heartbeat publications",
    )
    worker_registration_ttl_seconds: int = Field(
        default=30,
        description="TTL in seconds for worker registration in Redis (refreshed by heartbeat)",
    )
    memory_pressure_threshold: float = Field(
        default=0.85,
        description="Reject new forks when container memory usage exceeds this ratio (0.0-1.0)",
    )

    # ==========================================================================
    # Redis
    # ==========================================================================
    redis_url: str = Field(
        default="redis://localhost:6379/0", description="Redis connection URL"
    )

    # ==========================================================================
    # Security
    # ==========================================================================
    secret_key: str = Field(
        description="Secret key for JWT signing and encryption (BIFROST_SECRET_KEY env var required)",
        min_length=32,
    )

    algorithm: str = Field(default="HS256", description="JWT signing algorithm")

    access_token_expire_minutes: int = Field(
        default=30, description="Access token expiration time in minutes"
    )

    refresh_token_expire_days: int = Field(
        default=7, description="Refresh token expiration time in days"
    )

    jwt_issuer: str = Field(
        default="bifrost-api", description="JWT issuer claim for token validation"
    )

    jwt_audience: str = Field(
        default="bifrost-client", description="JWT audience claim for token validation"
    )

    oauth_require_mfa: bool = Field(
        default=False, description="If True, require MFA even for OAuth users"
    )

    # ==========================================================================
    # CORS
    # ==========================================================================
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated list of allowed CORS origins",
    )

    @computed_field
    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins into a list."""
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]

    # ==========================================================================
    # Object Storage
    # ==========================================================================
    object_storage_provider: str = Field(
        default="s3",
        description="Object storage backend provider: s3 or azure_blob",
    )

    @field_validator("object_storage_provider", mode="before")
    @classmethod
    def normalize_object_storage_provider(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower()
        return value

    s3_bucket: str | None = Field(
        default=None,
        description="S3 bucket name for workspace storage (required when S3 is configured)",
    )

    s3_endpoint_url: str | None = Field(
        default=None,
        description="S3 endpoint URL (None for AWS, local S3-compatible endpoint for self-hosted storage)",
    )

    s3_access_key: str | None = Field(default=None, description="S3 access key")

    s3_secret_key: str | None = Field(default=None, description="S3 secret key")

    s3_region: str = Field(default="us-east-1", description="S3 region")

    s3_public_endpoint_url: str | None = Field(
        default=None,
        description="Public S3 endpoint URL for presigned URLs (falls back to s3_endpoint_url)",
    )

    azure_blob_account_url: str | None = Field(
        default=None,
        description="Azure Blob account URL, e.g. https://acct.blob.core.windows.net",
    )

    azure_blob_container: str | None = Field(
        default=None,
        description="Azure Blob container name for workspace and upload storage",
    )

    azure_blob_auth: str = Field(
        default="default_credential",
        description="Azure Blob auth mode: default_credential or account_key",
    )

    azure_blob_account_key: str | None = Field(
        default=None,
        description="Azure Blob account key. Prefer managed identity/default credential in production.",
    )

    @computed_field
    @property
    def azure_blob_configured(self) -> bool:
        """Check if Azure Blob storage is configured."""
        if self.object_storage_provider != "azure_blob":
            return False
        if not self.azure_blob_account_url or not self.azure_blob_container:
            return False
        return self.azure_blob_auth == "default_credential" or bool(
            self.azure_blob_account_key
        )

    @computed_field
    @property
    def s3_configured(self) -> bool:
        """Check if S3 storage is configured."""
        return bool(self.s3_bucket and self.s3_access_key and self.s3_secret_key)

    # ==========================================================================
    # File Storage
    # ==========================================================================
    temp_location: str = Field(
        default_factory=default_temp_location,
        description="Path to temporary storage directory",
    )

    # ==========================================================================
    # Default User (for automated deployments and development)
    # ==========================================================================
    default_user_email: str | None = Field(
        default=None,
        description="Default admin user email (creates user on startup if set)",
    )

    default_user_password: str | None = Field(
        default=None, description="Default admin user password"
    )

    # ==========================================================================
    # MFA Settings
    # ==========================================================================
    mfa_enabled: bool = Field(
        default=True, description="Whether MFA is required for password authentication"
    )

    mfa_totp_issuer: str = Field(
        default="Bifrost", description="Issuer name for TOTP QR codes"
    )

    mfa_recovery_code_count: int = Field(
        default=10, description="Number of recovery codes to generate for MFA"
    )

    mfa_trusted_device_days: int = Field(
        default=30,
        description="Number of days a device stays trusted after MFA verification",
    )

    mfa_setup_token_expire_minutes: int = Field(
        default=15,
        description="MFA setup token expiration time in minutes (longer than verify for setup flow)",
    )

    mfa_verify_token_expire_minutes: int = Field(
        default=5,
        description="MFA verify token expiration time in minutes (during login)",
    )

    mfa_pending_validity_minutes: int = Field(
        default=10,
        description="How long a pending TOTP setup remains valid before regeneration",
    )

    mfa_totp_enrollment_window: int = Field(
        default=2,
        description="TOTP valid window for enrollment (+/- N*30 seconds, more lenient)",
    )

    mfa_totp_login_window: int = Field(
        default=1,
        description="TOTP valid window for login (+/- N*30 seconds, more strict)",
    )

    # ==========================================================================
    # WebAuthn/Passkeys
    # ==========================================================================
    webauthn_rp_id: str = Field(
        default="localhost",
        description="WebAuthn Relying Party ID (must match origin domain)",
    )

    webauthn_rp_name: str = Field(
        default="Bifrost", description="WebAuthn Relying Party display name"
    )

    webauthn_origin: str = Field(
        default="http://localhost:3000",
        description="WebAuthn expected origin URLs (comma-separated for multiple)",
    )

    @property
    def webauthn_origins(self) -> list[str]:
        """Parse webauthn_origin into a list of origins."""
        return [o.strip() for o in self.webauthn_origin.split(",") if o.strip()]

    # ==========================================================================
    # Public URL (used for MCP OAuth, workflow URLs, external links)
    # ==========================================================================
    public_url: str = Field(
        default="http://localhost:8000",
        description="Public URL for the Bifrost platform (used for MCP OAuth, workflow URLs, etc.)",
    )

    # ==========================================================================
    # Anthropic API (for Claude Agent SDK)
    # ==========================================================================
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Anthropic API key for Claude Agent SDK (ANTHROPIC_API_KEY or BIFROST_ANTHROPIC_API_KEY)",
    )

    # ==========================================================================
    # Server
    # ==========================================================================
    host: str = Field(default="0.0.0.0", description="Server host")

    port: int = Field(default=8000, description="Server port")

    # ==========================================================================
    # Computed Properties
    # ==========================================================================
    @computed_field
    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment == "development"

    @computed_field
    @property
    def is_testing(self) -> bool:
        """Check if running in testing mode."""
        return self.environment == "testing"

    @computed_field
    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.environment == "production"

    def validate_paths(self) -> None:
        """
        Validate that required filesystem paths exist.

        Creates temp directory if it doesn't exist.

        NOTE: We no longer pre-create /tmp/bifrost/workspace. Purpose-specific
        paths are created on-demand by the services that need them:
        - /tmp/bifrost/temp - Created here for temp operations
        """
        # Create temp location if it doesn't exist
        temp = Path(self.temp_location)
        temp.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache to ensure settings are only loaded once.
    """
    return Settings()
