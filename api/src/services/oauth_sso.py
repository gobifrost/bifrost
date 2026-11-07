"""
OAuth SSO Service - Single Sign-On with external identity providers.

Supports:
- Microsoft Entra ID (Azure AD)
- Google OAuth 2.0
- Generic OIDC providers

All OAuth flows use PKCE for enhanced security.
Configuration is stored in the database (system_configs table).
"""

import asyncio
import hashlib
import io
import logging
import secrets
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from authlib.oauth2.auth import ClientAuth
from authlib.oauth2.rfc7523 import PrivateKeyJWT
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.models import User, UserOAuthAccount
from src.services.oauth_config_service import OAuthConfigService
from src.services.oauth_provider import append_query_params
from src.services.oidc_rp import (
    GOOGLE_CLAIMS_OPTIONS,
    MICROSOFT_CLAIMS_OPTIONS,
    JwksProvider,
    OIDCClientFactory,
    build_oidc_relying_party,
    google_server_metadata,
    microsoft_server_metadata,
)

logger = logging.getLogger(__name__)

# The tenant id Entra gives personal Microsoft accounts (outlook.com, live.com, and
# personal accounts created on a work address). Bifrost signs in work accounts only.
MICROSOFT_CONSUMER_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"
# Google addresses that are not Workspace accounts, so carry no `hd` claim.
GOOGLE_CONSUMER_DOMAINS = {"gmail.com", "googlemail.com"}
# Signed-in user's profile photo. User.Read, already requested for Entra
# sign-in, covers this call. It is best-effort and must not fail login.
MICROSOFT_GRAPH_PHOTO_URL = "https://graph.microsoft.com/v1.0/me/photo/$value"
_MICROSOFT_PHOTO_TIMEOUT_SECONDS = 10.0
# Same ceiling as a manual avatar upload in the profile router.
_MICROSOFT_PHOTO_MAX_BYTES = 2 * 1024 * 1024
_MICROSOFT_PHOTO_MAX_PIXELS = 4096 * 4096
_MICROSOFT_PHOTO_CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png"}


@dataclass
class OAuthUserInfo:
    """Standardized user info from OAuth provider."""
    provider: str
    provider_user_id: str
    email: str
    name: str | None = None
    picture: str | None = None
    # This records only the provider's claim. Email authority for a new link is
    # established separately by ``validate_email_for_linking``.
    email_verified: bool | None = None
    raw_data: dict | None = None


@dataclass
class OAuthTokens:
    """OAuth tokens from provider."""
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str = "Bearer"
    id_token: str | None = None
    id_token_claims: dict[str, Any] | None = None
    scope: str | None = None


class OAuthError(Exception):
    """OAuth-related error."""
    pass


class OAuthService:
    """Service for OAuth SSO operations."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        oidc_client_factory: OIDCClientFactory = build_oidc_relying_party,
        jwks_provider: JwksProvider | None = None,
    ):
        self.db = db
        self._config_service = OAuthConfigService(db)
        self._oidc_client_factory = oidc_client_factory
        self._jwks_provider = jwks_provider
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
                "tenant_id": tenant,
                "authorize_url": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
                "token_url": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                "userinfo_url": "https://graph.microsoft.com/v1.0/me",
                "scopes": ["openid", "email", "profile", "User.Read"],
                **_client_auth_fields(config),
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
                **_client_auth_fields(config),
            }

        elif provider == "oidc":
            if not config or not config.discovery_url:
                raise OAuthError("OIDC provider is not configured")

            # Fetch OIDC discovery document to get endpoints
            discovery = await self._fetch_oidc_discovery(config.discovery_url)

            return {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "discovery_url": config.discovery_url,
                # Reuse the document already fetched for the endpoints above so
                # id_token verification does not fetch discovery a second time.
                "discovery": discovery,
                "authorize_url": discovery["authorization_endpoint"],
                "token_url": discovery["token_endpoint"],
                "userinfo_url": discovery.get("userinfo_endpoint"),
                "scopes": ["openid", "email", "profile"],
                "display_name": config.display_name or "SSO",
                **_client_auth_fields(config),
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
        nonce: str,
    ) -> str:
        """
        Generate the authorization URL for OAuth login.

        Args:
            provider: OAuth provider name
            redirect_uri: Callback URL
            state: CSRF state value
            code_verifier: PKCE code verifier
            nonce: OIDC nonce bound to this authorization request

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
            "nonce": nonce,
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
        nonce: str,
    ) -> OAuthTokens:
        """
        Exchange authorization code for tokens and verify the ``id_token``.

        Args:
            provider: OAuth provider name
            code: Authorization code from callback
            redirect_uri: Callback URL (must match authorize request)
            code_verifier: PKCE code verifier
            nonce: Nonce sent in the authorization request

        Returns:
            OAuthTokens with access token, verified ``id_token`` claims and
            optionally a refresh token
        """
        config = await self.get_provider_config(provider)
        token_url = config["token_url"]

        data = {
            "client_id": config["client_id"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        post_data, headers = _token_request_auth(config, token_url, data)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data=post_data,
                headers=headers,
            )

            if response.status_code != 200:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                error_msg = error_data.get("error_description", error_data.get("error", response.text))
                logger.error(f"Token exchange failed: {log_safe(error_msg)}")
                raise OAuthError(f"Token exchange failed: {error_msg}")

            token_data = response.json()

        id_token_claims = await self._verify_id_token(
            provider, config, token_data, nonce
        )

        return OAuthTokens(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_in=token_data.get("expires_in"),
            token_type=token_data.get("token_type", "Bearer"),
            id_token=token_data.get("id_token"),
            id_token_claims=id_token_claims,
            scope=token_data.get("scope"),
        )

    async def _id_token_metadata(
        self, provider: str, config: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Return discovery metadata and issuer options for id_token checks."""
        if provider == "oidc":
            # Generic OIDC uses the discovery document already fetched by
            # ``get_provider_config``; only fetch if the caller supplied none.
            discovery = config.get("discovery")
            if not discovery:
                discovery_url = config.get("discovery_url")
                if not discovery_url:
                    raise OAuthError("OIDC discovery is not configured")
                discovery = await self._fetch_oidc_discovery(discovery_url)
            return discovery, None
        if provider == "microsoft":
            return (
                microsoft_server_metadata(config.get("tenant_id")),
                MICROSOFT_CLAIMS_OPTIONS,
            )
        if provider == "google":
            return google_server_metadata(), GOOGLE_CLAIMS_OPTIONS
        raise OAuthError(f"Unknown OAuth provider: {provider}")

    async def _verify_id_token(
        self,
        provider: str,
        config: dict[str, Any],
        token_data: dict[str, Any],
        nonce: str,
    ) -> dict[str, Any]:
        """Verify the provider ``id_token``; never fall back to userinfo."""
        try:
            metadata, claims_options = await self._id_token_metadata(provider, config)
            client = self._oidc_client_factory(
                client_id=config["client_id"],
                client_secret=config.get("client_secret"),
                server_metadata=metadata,
                claims_options=claims_options,
                jwks_provider=self._jwks_provider,
            )
            try:
                return await client.parse_verified_id_token(token_data, nonce)
            finally:
                await client.aclose()
        except Exception as exc:
            # Log the real cause server-side; never echo Authlib/joserfc
            # exception text to the browser (the router returns detail=str(e)).
            logger.error(
                "OIDC id_token verification failed for %s: %s",
                log_safe(provider),
                log_safe(str(exc)),
            )
            raise OAuthError(
                "Could not verify the identity provider's response. Please try signing in again."
            ) from exc

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

        return self._parse_user_info(provider, user_data, tokens.id_token_claims)

    def _parse_user_info(
        self,
        provider: str,
        data: dict,
        claims: Mapping[str, Any] | None = None,
    ) -> OAuthUserInfo:
        """Extract a normalized OAuth identity without authorizing a new email link.

        For Microsoft the stored key stays the Graph ``id`` (the Entra ``oid``):
        the id_token ``sub`` is pairwise and app-scoped, so switching to it would
        re-key every existing account. The verified ``oid`` is instead
        cross-checked against the Graph id. Google and generic OIDC require
        userinfo ``sub`` to equal the id_token ``sub`` (OIDC Core 5.3.2), so the
        verified claim is used after that equality check.
        """
        if provider == "microsoft":
            graph_id = _provider_user_id(data.get("id"))
            if claims is not None:
                verified_oid = claims.get("oid")
                if not isinstance(verified_oid, str) or not verified_oid.strip():
                    raise OAuthError(
                        "Microsoft's verified token did not contain the expected account identifier (oid)"
                    )
                if verified_oid != graph_id:
                    raise OAuthError(
                        "Microsoft returned an id_token that doesn't match the signed-in account"
                    )
            return OAuthUserInfo(
                provider=provider,
                provider_user_id=graph_id,
                # Graph's `mail` is free text and never identifies a Bifrost user.
                email=_normalized_email(data.get("userPrincipalName")),
                name=data.get("displayName"),
                # Graph /me has no picture URL. The photo is fetched separately
                # from /me/photo/$value and stored on the user at sign-in.
                picture=None,
                email_verified=None,
                raw_data=data,
            )

        userinfo_sub = _provider_user_id(data.get("sub"))
        provider_user_id = userinfo_sub
        if claims is not None:
            provider_user_id = _provider_user_id(claims.get("sub"))
            if provider_user_id != userinfo_sub:
                raise OAuthError(
                    "The id_token subject does not match the userinfo subject"
                )

        return OAuthUserInfo(
            provider=provider,
            provider_user_id=provider_user_id,
            email=_normalized_email(data.get("email")),
            name=data.get("name"),
            picture=data.get("picture"),
            email_verified=data.get("email_verified") if isinstance(data.get("email_verified"), bool) else None,
            raw_data=data,
        )

    def validate_email_for_linking(self, user_info: OAuthUserInfo, tokens: OAuthTokens) -> None:
        """Require a provider-authoritative email before creating a new OAuth link.

        A linked account is authenticated solely by its provider ID, so current
        email claims cannot revoke access to an established mapping. Email is
        only relevant when creating that mapping or provisioning a user.
        """
        if not user_info.email:
            raise OAuthError("OAuth provider did not return email address")

        data = user_info.raw_data or {}

        if user_info.provider == "microsoft":
            tenant_id = _microsoft_tenant_id(tokens.id_token_claims)
            if tenant_id == MICROSOFT_CONSUMER_TENANT_ID:
                raise OAuthError("Personal Microsoft accounts can't sign in. Use your work or school account.")
            if "#ext#" in user_info.email:
                raise OAuthError("Guest accounts can't sign in. Sign in with your own organization's account.")
            return

        if user_info.provider == "google":
            if data.get("email_verified") is not True:
                raise OAuthError("Google hasn't verified this account's email address.")
            domain = user_info.email.rpartition("@")[2]
            hosted_domain = data.get("hd")
            if domain not in GOOGLE_CONSUMER_DOMAINS and (
                not isinstance(hosted_domain, str) or not hosted_domain.strip()
            ):
                raise OAuthError(f"Sign in with your {domain} Google Workspace account.")
            return

        # Generic OIDC is a trusted issuer selected through platform settings.
        # Preserve its existing email policy unless it explicitly denies verification.
        if data.get("email_verified") is False:
            raise OAuthError("Your identity provider hasn't verified this account's email address.")

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

    async def apply_microsoft_profile_photo(self, user: User, access_token: str) -> None:
        """Copy the Entra profile photo onto ``user`` when Graph returns one.

        A missing photo, a 404, or an unreadable body leaves whatever avatar
        the user already has in place. Network and HTTP failures return
        without raising so the caller can finish signing the user in.
        """
        photo = await fetch_microsoft_profile_photo(access_token)
        if photo is None:
            return
        user.avatar_data, user.avatar_content_type = photo

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


def _client_auth_fields(config: Any) -> dict[str, Any]:
    """Expose the provider's token-endpoint client authentication settings."""
    return {
        "token_endpoint_auth_method": config.token_endpoint_auth_method,
        "private_key": config.private_key,
        "private_key_kid": config.private_key_kid,
        "private_key_alg": config.private_key_alg,
    }


def _token_request_auth(
    config: dict[str, Any], token_url: str, data: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    """Attach client authentication using Authlib's own encoders/signers."""
    method = config.get("token_endpoint_auth_method") or "client_secret_post"
    client_id = config["client_id"]
    client_secret = config.get("client_secret") or ""

    if method == "private_key_jwt":
        private_key = config.get("private_key")
        if not private_key:
            raise OAuthError("private_key_jwt requires a private key")
        kid = config.get("private_key_kid")
        assertion = PrivateKeyJWT(
            token_endpoint=token_url,
            headers={"kid": kid} if kid else None,
            alg=config.get("private_key_alg") or "RS256",
        )
        client_auth = ClientAuth(client_id, private_key, assertion)
    elif method in {"client_secret_post", "client_secret_basic"}:
        client_auth = ClientAuth(client_id, client_secret, method)
    else:
        raise OAuthError(f"Unsupported token_endpoint_auth_method: {method}")

    _uri, headers, body = client_auth.prepare("POST", token_url, {}, urlencode(data))
    return (
        dict(parse_qsl(body, keep_blank_values=True)),
        {**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )


def _microsoft_tenant_id(claims: Mapping[str, Any] | None) -> str:
    """The ``tid`` claim of a Microsoft id_token verified by ``OIDCRelyingParty``."""
    if claims is None:
        raise OAuthError("Microsoft didn't return an ID token")
    tenant_id = claims.get("tid")
    if not tenant_id:
        raise OAuthError("Microsoft's ID token has no tenant")
    return str(tenant_id).lower()


def _provider_user_id(value: object) -> str:
    """Return a stable provider identifier or reject it before any lookup."""
    if not isinstance(value, str) or not value.strip():
        raise OAuthError("OAuth provider did not return a stable user ID")
    return value


def _normalized_email(value: object) -> str:
    """Normalize an optional email-like claim without deciding whether to trust it."""
    return value.strip().lower() if isinstance(value, str) else ""


async def _authorize_graph_photo_request(request: httpx.Request) -> None:
    """Keep the Entra access token on graph.microsoft.com.

    ``/me/photo/$value`` sometimes redirects to a short-lived download URL.
    That URL authenticates itself. The bearer token must not follow it to
    another host.
    """
    if request.url.scheme != "https" or request.url.host != "graph.microsoft.com":
        request.headers.pop("Authorization", None)


def _microsoft_photo_content_type(body: bytes) -> str | None:
    """Return image/jpeg or image/png when ``body`` is a bounded photo."""
    if not body or len(body) > _MICROSOFT_PHOTO_MAX_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(body)) as image:
            content_type = _MICROSOFT_PHOTO_CONTENT_TYPES.get(image.format or "")
            if content_type is None or image.width < 1 or image.height < 1:
                return None
            if image.width * image.height > _MICROSOFT_PHOTO_MAX_PIXELS:
                return None
            image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        logger.info("Microsoft profile photo discarded: %s", type(exc).__name__)
        return None
    return content_type


async def fetch_microsoft_profile_photo(access_token: str) -> tuple[bytes, str] | None:
    """Fetch the signed-in user's Graph profile photo.

    Returns ``(bytes, content_type)`` or None. A missing photo (including
    Graph's 404), a timeout, a non-image body, or any other photo error
    returns None and never raises.
    """
    if not access_token:
        return None
    try:
        async with asyncio.timeout(_MICROSOFT_PHOTO_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=_MICROSOFT_PHOTO_TIMEOUT_SECONDS,
                follow_redirects=True,
                event_hooks={"request": [_authorize_graph_photo_request]},
            ) as client:
                async with client.stream(
                    "GET",
                    MICROSOFT_GRAPH_PHOTO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                ) as response:
                    if response.url.scheme != "https" or response.status_code == 404:
                        return None
                    if response.status_code != 200:
                        logger.info("Microsoft profile photo unavailable: HTTP %s", response.status_code)
                        return None
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > _MICROSOFT_PHOTO_MAX_BYTES:
                            return None
                        body.extend(chunk)
    except (httpx.HTTPError, TimeoutError) as exc:
        logger.info("Microsoft profile photo request failed: %s", type(exc).__name__)
        return None

    photo = bytes(body)
    content_type = _microsoft_photo_content_type(photo)
    if content_type is None:
        return None
    return photo, content_type
