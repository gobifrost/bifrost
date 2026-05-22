"""
OAuth SSO Service - Single Sign-On with external identity providers.

Supports:
- Microsoft Entra ID (Azure AD)
- Google OAuth 2.0
- Generic OIDC providers

All OAuth flows use PKCE for enhanced security.
Configuration is stored in the database (system_configs table).
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.models import User, UserOAuthAccount
from src.services.oauth_config_service import OAuthConfigService
from src.services.oauth_provider import append_query_params

logger = logging.getLogger(__name__)


@dataclass
class OAuthUserInfo:
    """Standardized user info from OAuth provider."""
    provider: str
    provider_user_id: str
    email: str
    name: str | None = None
    picture: str | None = None
    email_verified: bool = False
    raw_data: dict | None = None


@dataclass
class OAuthTokens:
    """OAuth tokens from provider."""
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str = "Bearer"
    id_token: str | None = None
    scope: str | None = None


class OAuthError(Exception):
    """OAuth-related error."""
    pass


class OAuthService:
    """Service for OAuth SSO operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._config_service = OAuthConfigService(db)
        # Cache for OIDC discovery documents
        self._oidc_discovery_cache: dict[str, dict[str, Any]] = {}

    # ========================================================================
    # PKCE Utilities
    # ========================================================================

    @staticmethod
    def generate_code_verifier() -> str:
        """Generate a cryptographically random code verifier for PKCE."""
        return secrets.token_urlsafe(64)[:128]

    @staticmethod
    def generate_code_challenge(verifier: str) -> str:
        """Generate S256 code challenge from verifier."""
        digest = hashlib.sha256(verifier.encode()).digest()
        # Base64url encode without padding
        import base64
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    @staticmethod
    def generate_state() -> str:
        """Generate a random state value for CSRF protection."""
        return secrets.token_urlsafe(32)

    # ========================================================================
    # Provider Configuration
    # ========================================================================

    async def _fetch_oidc_discovery(self, discovery_url: str) -> dict[str, Any]:
        """Fetch and cache OIDC discovery document."""
        if discovery_url in self._oidc_discovery_cache:
            return self._oidc_discovery_cache[discovery_url]

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(discovery_url)
            if response.status_code != 200:
                raise OAuthError(f"Failed to fetch OIDC discovery: HTTP {response.status_code}")

            data = response.json()

            # Validate required fields
            required = ["authorization_endpoint", "token_endpoint", "issuer"]
            missing = [f for f in required if f not in data]
            if missing:
                raise OAuthError(f"OIDC discovery missing required fields: {', '.join(missing)}")

            self._oidc_discovery_cache[discovery_url] = data
            return data

    async def get_provider_config(self, provider: str) -> dict[str, Any]:
        """
        Get OAuth configuration for a provider.

        Args:
            provider: Provider name (microsoft, google, oidc)

        Returns:
            Provider configuration dict

        Raises:
            OAuthError: If provider is not configured
        """
        config = await self._config_service.get_provider_config(provider)  # type: ignore

        if provider == "microsoft":
            if not config:
                raise OAuthError("Microsoft OAuth is not configured")

            tenant = config.tenant_id or "common"
            return {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "authorize_url": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
                "token_url": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                "userinfo_url": "https://graph.microsoft.com/v1.0/me",
                "scopes": ["openid", "email", "profile", "User.Read"],
            }

        elif provider == "google":
            if not config:
                raise OAuthError("Google OAuth is not configured")

            return {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_url": "https://oauth2.googleapis.com/token",
                "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
                "scopes": ["openid", "email", "profile"],
            }

        elif provider == "oidc":
            if not config or not config.discovery_url:
                raise OAuthError("OIDC provider is not configured")

            # Fetch OIDC discovery document to get endpoints
            discovery = await self._fetch_oidc_discovery(config.discovery_url)

            return {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "authorize_url": discovery["authorization_endpoint"],
                "token_url": discovery["token_endpoint"],
                "userinfo_url": discovery.get("userinfo_endpoint"),
                "scopes": ["openid", "email", "profile"],
                "display_name": config.display_name or "SSO",
            }

        else:
            raise OAuthError(f"Unknown OAuth provider: {provider}")

    async def get_available_providers(self) -> list[str]:
        """Get list of configured OAuth providers."""
        return await self._config_service.get_available_providers()

    # ========================================================================
    # OAuth Flow
    # ========================================================================

    async def get_authorization_url(
        self,
        provider: str,
        redirect_uri: str,
        state: str,
        code_verifier: str,
    ) -> str:
        """
        Generate the authorization URL for OAuth login.

        Args:
            provider: OAuth provider name
            redirect_uri: Callback URL
            state: CSRF state value
            code_verifier: PKCE code verifier

        Returns:
            Authorization URL to redirect user to
        """
        config = await self.get_provider_config(provider)
        code_challenge = self.generate_code_challenge(code_verifier)

        params = {
            "client_id": config["client_id"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(config["scopes"]),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        # Microsoft-specific params
        if provider == "microsoft":
            params["response_mode"] = "query"

        # Google-specific params
        if provider == "google":
            params["access_type"] = "offline"
            params["prompt"] = "consent"

        return append_query_params(config["authorize_url"], params)

    async def exchange_code_for_tokens(
        self,
        provider: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthTokens:
        """
        Exchange authorization code for tokens.

        Args:
            provider: OAuth provider name
            code: Authorization code from callback
            redirect_uri: Callback URL (must match authorize request)
            code_verifier: PKCE code verifier

        Returns:
            OAuthTokens with access token and optionally refresh token
        """
        config = await self.get_provider_config(provider)

        data = {
            "client_id": config["client_id"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }

        # Add client secret if configured
        if config.get("client_secret"):
            data["client_secret"] = config["client_secret"]

        async with httpx.AsyncClient() as client:
            response = await client.post(
                config["token_url"],
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                error_msg = error_data.get("error_description", error_data.get("error", response.text))
                logger.error(f"Token exchange failed: {log_safe(error_msg)}")
                raise OAuthError(f"Token exchange failed: {error_msg}")

            token_data = response.json()

        return OAuthTokens(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_in=token_data.get("expires_in"),
            token_type=token_data.get("token_type", "Bearer"),
            id_token=token_data.get("id_token"),
            scope=token_data.get("scope"),
        )

    async def get_user_info(
        self,
        provider: str,
        tokens: OAuthTokens,
    ) -> OAuthUserInfo:
        """
        Get user info from OAuth provider.

        Args:
            provider: OAuth provider name
            tokens: OAuth tokens from exchange

        Returns:
            Standardized user info
        """
        config = await self.get_provider_config(provider)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                config["userinfo_url"],
                headers={"Authorization": f"{tokens.token_type} {tokens.access_token}"},
            )

            if response.status_code != 200:
                raise OAuthError(f"Failed to get user info: {response.text}")

            user_data = response.json()

        return self._parse_user_info(provider, user_data)

    def _parse_user_info(self, provider: str, data: dict) -> OAuthUserInfo:
        """Parse provider-specific user info into standard format."""
        if provider == "microsoft":
            return OAuthUserInfo(
                provider=provider,
                provider_user_id=data.get("id", ""),
                email=data.get("mail") or data.get("userPrincipalName", ""),
                name=data.get("displayName"),
                picture=None,  # Microsoft requires separate Graph API call for photo
                email_verified=True,  # Microsoft always verifies emails
                raw_data=data,
            )

        elif provider == "google":
            return OAuthUserInfo(
                provider=provider,
                provider_user_id=data.get("sub", ""),
                email=data.get("email", ""),
                name=data.get("name"),
                picture=data.get("picture"),
                email_verified=data.get("email_verified", False),
                raw_data=data,
            )

        else:
            # Generic OIDC
            return OAuthUserInfo(
                provider=provider,
                provider_user_id=data.get("sub", ""),
                email=data.get("email", ""),
                name=data.get("name"),
                picture=data.get("picture"),
                email_verified=data.get("email_verified", False),
                raw_data=data,
            )

    # ========================================================================
    # OAuth Account Management
    # ========================================================================

    async def get_oauth_account(
        self,
        provider: str,
        provider_user_id: str,
    ) -> UserOAuthAccount | None:
        """Get OAuth account by provider and provider user ID."""
        result = await self.db.execute(
            select(UserOAuthAccount).where(
                UserOAuthAccount.provider_id == provider,
                UserOAuthAccount.provider_user_id == provider_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_user_oauth_accounts(self, user_id: UUID) -> list[UserOAuthAccount]:
        """Get all OAuth accounts linked to a user."""
        result = await self.db.execute(
            select(UserOAuthAccount).where(UserOAuthAccount.user_id == user_id)
        )
        return list(result.scalars().all())

    async def link_oauth_account(
        self,
        user: User,
        user_info: OAuthUserInfo,
        tokens: OAuthTokens | None = None,
    ) -> UserOAuthAccount:
        """
        Link an OAuth account to a user.

        Args:
            user: User to link account to
            user_info: OAuth user info
            tokens: Optional tokens to store (encrypted)

        Returns:
            Created or updated OAuth account
        """
        # Check if already linked
        existing = await self.get_oauth_account(
            user_info.provider,
            user_info.provider_user_id,
        )

        if existing:
            # Update last login timestamp
            existing.last_login = datetime.now(timezone.utc)
            await self.db.flush()
            return existing

        # Create new account
        oauth_account = UserOAuthAccount(
            user_id=user.id,
            provider_id=user_info.provider,
            provider_user_id=user_info.provider_user_id,
            email=user_info.email,
        )

        self.db.add(oauth_account)
        await self.db.flush()

        logger.info(
            f"Linked OAuth account: {user_info.provider} for user {user.email}",
            extra={
                "user_id": str(user.id),
                "provider": user_info.provider,
                "provider_user_id": user_info.provider_user_id,
            }
        )

        return oauth_account

    async def unlink_oauth_account(
        self,
        user_id: UUID,
        provider: str,
    ) -> bool:
        """
        Unlink an OAuth account from a user.

        Args:
            user_id: User ID
            provider: OAuth provider name

        Returns:
            True if account was found and deleted
        """
        from sqlalchemy import delete

        result = await self.db.execute(
            delete(UserOAuthAccount).where(
                UserOAuthAccount.user_id == user_id,
                UserOAuthAccount.provider_id == provider,
            )
        )
        await self.db.flush()
        return result.rowcount > 0

    async def find_user_by_oauth(
        self,
        provider: str,
        provider_user_id: str,
    ) -> User | None:
        """
        Find a user by their OAuth account.

        Args:
            provider: OAuth provider name
            provider_user_id: Provider's user ID

        Returns:
            User if found
        """
        result = await self.db.execute(
            select(User)
            .join(UserOAuthAccount)
            .where(
                UserOAuthAccount.provider_id == provider,
                UserOAuthAccount.provider_user_id == provider_user_id,
            )
        )
        return result.scalar_one_or_none()
