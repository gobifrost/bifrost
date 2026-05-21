"""
OAuth SSO Configuration Service.

Manages OAuth SSO provider configurations stored in the system_configs table.
Handles encryption of client secrets and provides a unified interface for
accessing OAuth provider settings.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.core.security import decrypt_secret, encrypt_secret
from src.models.contracts.oauth_config import (
    OAUTH_CONFIG_CATEGORY,
    OAUTH_GOOGLE_CLIENT_ID,
    OAUTH_GOOGLE_CLIENT_SECRET,
    OAUTH_MICROSOFT_CLIENT_ID,
    OAUTH_MICROSOFT_CLIENT_SECRET,
    OAUTH_MICROSOFT_TENANT_ID,
    OAUTH_OIDC_CLIENT_ID,
    OAUTH_OIDC_CLIENT_SECRET,
    OAUTH_OIDC_DISCOVERY_URL,
    OAUTH_OIDC_DISPLAY_NAME,
    GoogleOAuthConfigRequest,
    MicrosoftOAuthConfigRequest,
    OAuthConfigTestResponse,
    OAuthProviderConfigResponse,
    OAuthSSOProvider,
    OIDCConfigRequest,
)
from src.models.orm.config import SystemConfig

logger = logging.getLogger(__name__)


@dataclass
class OAuthProviderConfig:
    """Internal representation of OAuth provider configuration."""

    provider: OAuthSSOProvider
    client_id: str
    client_secret: str
    # Microsoft-specific
    tenant_id: str | None = None
    # OIDC-specific
    discovery_url: str | None = None
    display_name: str | None = None

    @property
    def is_complete(self) -> bool:
        """Check if all required fields are set."""
        if not self.client_id or not self.client_secret:
            return False
        if self.provider == "microsoft":
            return True  # tenant_id defaults to 'common'
        if self.provider == "google":
            return True
        if self.provider == "oidc":
            return bool(self.discovery_url)
        return False


class OAuthConfigService:
    """Service for managing OAuth SSO provider configurations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_config_value(self, key: str) -> str | None:
        """Get a config value from the database."""
        result = await self.db.execute(
            select(SystemConfig).where(
                SystemConfig.category == OAUTH_CONFIG_CATEGORY,
                SystemConfig.key == key,
                SystemConfig.organization_id.is_(None),  # Global config only
            )
        )
        config = result.scalar_one_or_none()
        if config and config.value_json:
            return config.value_json.get("value")
        return None

    async def _get_secret_value(self, key: str) -> str | None:
        """Get and decrypt a secret config value from the database."""
        encrypted = await self._get_config_value(key)
        if encrypted:
            try:
                return decrypt_secret(encrypted)
            except Exception as e:
                logger.error(
                    "Failed to decrypt OAuth config secret: %s",
                    e.__class__.__name__,
                )
                return None
        return None

    async def _set_config_value(
        self,
        key: str,
        value: str,
        is_secret: bool = False,
        updated_by: str = "system",
    ) -> None:
        """Set a config value in the database."""
        stored_value = encrypt_secret(value) if is_secret else value

        # Check if exists
        result = await self.db.execute(
            select(SystemConfig).where(
                SystemConfig.category == OAUTH_CONFIG_CATEGORY,
                SystemConfig.key == key,
                SystemConfig.organization_id.is_(None),
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value_json = {"value": stored_value}
            existing.updated_by = updated_by
        else:
            config = SystemConfig(
                category=OAUTH_CONFIG_CATEGORY,
                key=key,
                value_json={"value": stored_value},
                organization_id=None,
                created_by=updated_by,
                updated_by=updated_by,
            )
            self.db.add(config)

        await self.db.flush()

    async def _delete_config_keys(self, keys: list[str]) -> None:
        """Delete multiple config keys."""
        await self.db.execute(
            delete(SystemConfig).where(
                SystemConfig.category == OAUTH_CONFIG_CATEGORY,
                SystemConfig.key.in_(keys),
                SystemConfig.organization_id.is_(None),
            )
        )
        await self.db.flush()

    # =========================================================================
    # Provider Configuration CRUD
    # =========================================================================

    async def get_provider_config(
        self, provider: OAuthSSOProvider
    ) -> OAuthProviderConfig | None:
        """
        Get OAuth configuration for a provider.

        Returns None if the provider is not configured.
        """
        if provider == "microsoft":
            client_id = await self._get_config_value(OAUTH_MICROSOFT_CLIENT_ID)
            client_secret = await self._get_secret_value(OAUTH_MICROSOFT_CLIENT_SECRET)
            tenant_id = await self._get_config_value(OAUTH_MICROSOFT_TENANT_ID)

            if not client_id or not client_secret:
                return None

            return OAuthProviderConfig(
                provider="microsoft",
                client_id=client_id,
                client_secret=client_secret,
                tenant_id=tenant_id or "common",
            )

        elif provider == "google":
            client_id = await self._get_config_value(OAUTH_GOOGLE_CLIENT_ID)
            client_secret = await self._get_secret_value(OAUTH_GOOGLE_CLIENT_SECRET)

            if not client_id or not client_secret:
                return None

            return OAuthProviderConfig(
                provider="google",
                client_id=client_id,
                client_secret=client_secret,
            )

        elif provider == "oidc":
            discovery_url = await self._get_config_value(OAUTH_OIDC_DISCOVERY_URL)
            client_id = await self._get_config_value(OAUTH_OIDC_CLIENT_ID)
            client_secret = await self._get_secret_value(OAUTH_OIDC_CLIENT_SECRET)
            display_name = await self._get_config_value(OAUTH_OIDC_DISPLAY_NAME)

            if not discovery_url or not client_id or not client_secret:
                return None

            return OAuthProviderConfig(
                provider="oidc",
                client_id=client_id,
                client_secret=client_secret,
                discovery_url=discovery_url,
                display_name=display_name or "SSO",
            )

        return None

    async def set_microsoft_config(
        self,
        config: MicrosoftOAuthConfigRequest,
        updated_by: str = "system",
    ) -> None:
        """Set Microsoft OAuth configuration."""
        await self._set_config_value(
            OAUTH_MICROSOFT_CLIENT_ID, config.client_id, updated_by=updated_by
        )
        await self._set_config_value(
            OAUTH_MICROSOFT_CLIENT_SECRET,
            config.client_secret,
            is_secret=True,
            updated_by=updated_by,
        )
        await self._set_config_value(
            OAUTH_MICROSOFT_TENANT_ID, config.tenant_id, updated_by=updated_by
        )
        logger.info(f"Microsoft OAuth config updated by {updated_by}")

    async def set_google_config(
        self,
        config: GoogleOAuthConfigRequest,
        updated_by: str = "system",
    ) -> None:
        """Set Google OAuth configuration."""
        await self._set_config_value(
            OAUTH_GOOGLE_CLIENT_ID, config.client_id, updated_by=updated_by
        )
        await self._set_config_value(
            OAUTH_GOOGLE_CLIENT_SECRET,
            config.client_secret,
            is_secret=True,
            updated_by=updated_by,
        )
        logger.info(f"Google OAuth config updated by {updated_by}")

    async def set_oidc_config(
        self,
        config: OIDCConfigRequest,
        updated_by: str = "system",
    ) -> None:
        """Set OIDC provider configuration."""
        await self._set_config_value(
            OAUTH_OIDC_DISCOVERY_URL, config.discovery_url, updated_by=updated_by
        )
        await self._set_config_value(
            OAUTH_OIDC_CLIENT_ID, config.client_id, updated_by=updated_by
        )
        await self._set_config_value(
            OAUTH_OIDC_CLIENT_SECRET,
            config.client_secret,
            is_secret=True,
            updated_by=updated_by,
        )
        await self._set_config_value(
            OAUTH_OIDC_DISPLAY_NAME, config.display_name, updated_by=updated_by
        )
        logger.info(f"OIDC config updated by {updated_by}")

    async def delete_provider_config(self, provider: OAuthSSOProvider) -> bool:
        """Delete all configuration for a provider."""
        if provider == "microsoft":
            keys = [
                OAUTH_MICROSOFT_CLIENT_ID,
                OAUTH_MICROSOFT_CLIENT_SECRET,
                OAUTH_MICROSOFT_TENANT_ID,
            ]
        elif provider == "google":
            keys = [
                OAUTH_GOOGLE_CLIENT_ID,
                OAUTH_GOOGLE_CLIENT_SECRET,
            ]
        elif provider == "oidc":
            keys = [
                OAUTH_OIDC_DISCOVERY_URL,
                OAUTH_OIDC_CLIENT_ID,
                OAUTH_OIDC_CLIENT_SECRET,
                OAUTH_OIDC_DISPLAY_NAME,
            ]
        else:
            return False

        await self._delete_config_keys(keys)
        logger.info(f"OAuth config deleted for provider: {log_safe(provider)}")
        return True

    # =========================================================================
    # List and Status
    # =========================================================================

    async def get_all_provider_configs(self) -> list[OAuthProviderConfigResponse]:
        """Get configuration status for all providers."""
        providers: list[OAuthProviderConfigResponse] = []

        for provider in ["microsoft", "google", "oidc"]:
            config = await self.get_provider_config(provider)  # type: ignore
            if config:
                providers.append(
                    OAuthProviderConfigResponse(
                        provider=provider,  # type: ignore
                        configured=config.is_complete,
                        client_id=config.client_id,
                        client_secret_set=bool(config.client_secret),
                        tenant_id=config.tenant_id if provider == "microsoft" else None,
                        discovery_url=config.discovery_url
                        if provider == "oidc"
                        else None,
                        display_name=config.display_name
                        if provider == "oidc"
                        else None,
                    )
                )
            else:
                providers.append(
                    OAuthProviderConfigResponse(
                        provider=provider,  # type: ignore
                        configured=False,
                    )
                )

        return providers

    async def get_available_providers(self) -> list[str]:
        """Get list of fully configured OAuth providers."""
        providers = []
        for provider in ["microsoft", "google", "oidc"]:
            config = await self.get_provider_config(provider)  # type: ignore
            if config and config.is_complete:
                providers.append(provider)
        return providers

    # =========================================================================
    # Validation / Testing
    # =========================================================================

    async def test_microsoft_config(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
    ) -> OAuthConfigTestResponse:
        """Test Microsoft OAuth configuration by checking the discovery endpoint."""
        # If the caller didn't pass full creds, require a saved config — and
        # fill in the tenant from it so the discovery URL points at the
        # correct directory. (client_id/client_secret are validated by the
        # presence check above; they aren't used directly by the discovery
        # probe, which is keyed only on tenant.)
        if not client_id or not client_secret:
            config = await self.get_provider_config("microsoft")
            if not config:
                return OAuthConfigTestResponse(
                    success=False,
                    message="Microsoft OAuth is not configured",
                )
            tenant_id = tenant_id or config.tenant_id

        tenant = tenant_id or "common"
        discovery_url = f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(discovery_url)
                if response.status_code == 200:
                    data = response.json()
                    return OAuthConfigTestResponse(
                        success=True,
                        message=f"Successfully connected to Microsoft Entra ID (tenant: {tenant})",
                        details={
                            "issuer": data.get("issuer"),
                            "authorization_endpoint": data.get(
                                "authorization_endpoint"
                            ),
                            "token_endpoint": data.get("token_endpoint"),
                        },
                    )
                else:
                    return OAuthConfigTestResponse(
                        success=False,
                        message=f"Failed to reach Microsoft discovery endpoint: HTTP {response.status_code}",
                    )
        except httpx.RequestError as e:
            return OAuthConfigTestResponse(
                success=False,
                message=f"Network error connecting to Microsoft: {e!s}",
            )

    async def test_google_config(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> OAuthConfigTestResponse:
        """Test Google OAuth configuration by checking the discovery endpoint."""
        # If the caller didn't pass full creds, require a saved config. The
        # discovery probe doesn't need client_id/client_secret — Google's
        # OIDC discovery URL is fixed — but we still gate the probe on having
        # a configured provider so the UI surfaces "not configured" cleanly.
        if not client_id or not client_secret:
            config = await self.get_provider_config("google")
            if not config:
                return OAuthConfigTestResponse(
                    success=False,
                    message="Google OAuth is not configured",
                )

        discovery_url = "https://accounts.google.com/.well-known/openid-configuration"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(discovery_url)
                if response.status_code == 200:
                    data = response.json()
                    return OAuthConfigTestResponse(
                        success=True,
                        message="Successfully connected to Google OAuth",
                        details={
                            "issuer": data.get("issuer"),
                            "authorization_endpoint": data.get(
                                "authorization_endpoint"
                            ),
                            "token_endpoint": data.get("token_endpoint"),
                        },
                    )
                else:
                    return OAuthConfigTestResponse(
                        success=False,
                        message=f"Failed to reach Google discovery endpoint: HTTP {response.status_code}",
                    )
        except httpx.RequestError as e:
            return OAuthConfigTestResponse(
                success=False,
                message=f"Network error connecting to Google: {e!s}",
            )

    async def test_oidc_config(
        self,
        discovery_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> OAuthConfigTestResponse:
        """Test OIDC configuration by fetching and validating the discovery document."""
        # If the caller didn't pass full creds + discovery URL, fill the
        # discovery URL from saved config so we can still probe. The probe
        # itself only needs the discovery URL; client_id/client_secret are
        # validated by the presence check above.
        if not discovery_url or not client_id or not client_secret:
            config = await self.get_provider_config("oidc")
            if not config:
                return OAuthConfigTestResponse(
                    success=False,
                    message="OIDC is not configured",
                )
            discovery_url = discovery_url or config.discovery_url

        if not discovery_url:
            return OAuthConfigTestResponse(
                success=False,
                message="OIDC discovery URL is required",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(discovery_url)
                if response.status_code == 200:
                    data = response.json()
                    # Validate required OIDC fields
                    required_fields = [
                        "issuer",
                        "authorization_endpoint",
                        "token_endpoint",
                    ]
                    missing = [f for f in required_fields if f not in data]
                    if missing:
                        return OAuthConfigTestResponse(
                            success=False,
                            message=f"Discovery document missing required fields: {', '.join(missing)}",
                        )

                    return OAuthConfigTestResponse(
                        success=True,
                        message=f"Successfully connected to OIDC provider: {data.get('issuer')}",
                        details={
                            "issuer": data.get("issuer"),
                            "authorization_endpoint": data.get(
                                "authorization_endpoint"
                            ),
                            "token_endpoint": data.get("token_endpoint"),
                            "userinfo_endpoint": data.get("userinfo_endpoint"),
                            "scopes_supported": data.get("scopes_supported", [])[:10],
                        },
                    )
                else:
                    return OAuthConfigTestResponse(
                        success=False,
                        message=f"Failed to fetch OIDC discovery document: HTTP {response.status_code}",
                    )
        except httpx.RequestError as e:
            return OAuthConfigTestResponse(
                success=False,
                message=f"Network error fetching OIDC discovery: {e!s}",
            )

    async def test_provider_config(
        self,
        provider: OAuthSSOProvider,
        test_data: dict[str, Any] | None = None,
    ) -> OAuthConfigTestResponse:
        """Test configuration for a specific provider."""
        test_data = test_data or {}

        if provider == "microsoft":
            return await self.test_microsoft_config(
                client_id=test_data.get("client_id"),
                client_secret=test_data.get("client_secret"),
                tenant_id=test_data.get("tenant_id"),
            )
        elif provider == "google":
            return await self.test_google_config(
                client_id=test_data.get("client_id"),
                client_secret=test_data.get("client_secret"),
            )
        elif provider == "oidc":
            return await self.test_oidc_config(
                discovery_url=test_data.get("discovery_url"),
                client_id=test_data.get("client_id"),
                client_secret=test_data.get("client_secret"),
            )
        else:
            return OAuthConfigTestResponse(
                success=False,
                message=f"Unknown provider: {provider}",
            )
