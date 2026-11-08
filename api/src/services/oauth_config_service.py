"""
OAuth SSO Configuration Service.

Manages OAuth SSO provider configurations stored in the system_configs table.
Handles encryption of client secrets and provides a unified interface for
accessing OAuth provider settings.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.core.security import decrypt_secret, encrypt_secret
from src.models.contracts.oauth_config import (
    OAUTH_CONFIG_CATEGORY,
    OAUTH_PRIVATE_KEY_ALGS,
    OAUTH_GOOGLE_CLIENT_ID,
    OAUTH_GOOGLE_CLIENT_SECRET,
    OAUTH_GOOGLE_PRIVATE_KEY,
    OAUTH_GOOGLE_PRIVATE_KEY_ALG,
    OAUTH_GOOGLE_PRIVATE_KEY_KID,
    OAUTH_GOOGLE_SCOPES,
    OAUTH_GOOGLE_TOKEN_ENDPOINT_AUTH_METHOD,
    OAUTH_MICROSOFT_CLIENT_ID,
    OAUTH_MICROSOFT_CLIENT_SECRET,
    OAUTH_MICROSOFT_PRIVATE_KEY,
    OAUTH_MICROSOFT_PRIVATE_KEY_ALG,
    OAUTH_MICROSOFT_PRIVATE_KEY_KID,
    OAUTH_MICROSOFT_SCOPES,
    OAUTH_MICROSOFT_TENANT_ID,
    OAUTH_MICROSOFT_TOKEN_ENDPOINT_AUTH_METHOD,
    OAUTH_LOGIN_PREFERENCE,
    OAUTH_OIDC_CLIENT_ID,
    OAUTH_OIDC_CLIENT_SECRET,
    OAUTH_OIDC_DISCOVERY_URL,
    OAUTH_OIDC_DISPLAY_NAME,
    OAUTH_OIDC_PRIVATE_KEY,
    OAUTH_OIDC_PRIVATE_KEY_ALG,
    OAUTH_OIDC_PRIVATE_KEY_KID,
    OAUTH_OIDC_SCOPES,
    OAUTH_OIDC_TOKEN_ENDPOINT_AUTH_METHOD,
    GoogleOAuthConfigRequest,
    MicrosoftOAuthConfigRequest,
    OAuthConfigTestResponse,
    OAuthLoginPreference,
    OAuthProviderConfigResponse,
    OAuthSSOProvider,
    OAuthTokenEndpointAuthMethod,
    OIDCConfigRequest,
)
from src.models.orm.config import SystemConfig
from src.models.orm.users import User

logger = logging.getLogger(__name__)


class OAuthLoginPreferenceError(ValueError):
    """Raised when a preferred SSO provider cannot be used."""


class OAuthProviderPreferenceConflict(ValueError):
    """Raised when deleting the provider used by an active preference."""


class OAuthConfigValidationError(ValueError):
    """Raised when a provider configuration cannot be saved as requested."""


_TOKEN_ENDPOINT_AUTH_METHODS: tuple[OAuthTokenEndpointAuthMethod, ...] = (
    "client_secret_post",
    "client_secret_basic",
    "private_key_jwt",
)

_DEFAULT_SCOPES: dict[str, list[str]] = {
    "microsoft": ["openid", "email", "profile", "User.Read"],
    "google": ["openid", "email", "profile"],
    "oidc": ["openid", "email", "profile"],
}

_PROVIDER_SCOPES_KEY: dict[str, str] = {
    "microsoft": OAUTH_MICROSOFT_SCOPES,
    "google": OAUTH_GOOGLE_SCOPES,
    "oidc": OAUTH_OIDC_SCOPES,
}

_PROVIDER_SECRET_KEYS: dict[str, str] = {
    "microsoft": OAUTH_MICROSOFT_CLIENT_SECRET,
    "google": OAUTH_GOOGLE_CLIENT_SECRET,
    "oidc": OAUTH_OIDC_CLIENT_SECRET,
}

_PROVIDER_AUTH_KEYS = {
    "microsoft": (
        OAUTH_MICROSOFT_TOKEN_ENDPOINT_AUTH_METHOD,
        OAUTH_MICROSOFT_PRIVATE_KEY,
        OAUTH_MICROSOFT_PRIVATE_KEY_KID,
        OAUTH_MICROSOFT_PRIVATE_KEY_ALG,
    ),
    "google": (
        OAUTH_GOOGLE_TOKEN_ENDPOINT_AUTH_METHOD,
        OAUTH_GOOGLE_PRIVATE_KEY,
        OAUTH_GOOGLE_PRIVATE_KEY_KID,
        OAUTH_GOOGLE_PRIVATE_KEY_ALG,
    ),
    "oidc": (
        OAUTH_OIDC_TOKEN_ENDPOINT_AUTH_METHOD,
        OAUTH_OIDC_PRIVATE_KEY,
        OAUTH_OIDC_PRIVATE_KEY_KID,
        OAUTH_OIDC_PRIVATE_KEY_ALG,
    ),
}


#: Refusal details for SSO-only enforcement (routers share these so the
#: login, MFA, registration, passkey, and profile surfaces stay consistent).
PASSWORD_SSO_ONLY_DETAIL = "Password login is disabled (SSO-only). Sign in with SSO."
PASSKEY_SSO_ONLY_DETAIL = "Passkey login is disabled (SSO-only)."


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
    # Token-endpoint client authentication
    token_endpoint_auth_method: str = "client_secret_post"
    private_key: str | None = None
    private_key_kid: str | None = None
    private_key_alg: str | None = None
    # Requested OAuth scopes
    scopes: list[str] | None = None

    @property
    def is_complete(self) -> bool:
        """Check if all required fields are set."""
        if not self.client_id:
            return False
        if self.token_endpoint_auth_method == "private_key_jwt":
            if not self.private_key:
                return False
        elif not self.client_secret:
            return False
        if self.provider == "microsoft":
            return True  # tenant_id defaults to 'common'
        if self.provider == "google":
            return True
        if self.provider == "oidc":
            return bool(self.discovery_url)
        return False


# OAuth 2.0 scope-token grammar (RFC 6749 §3.3): visible ASCII minus space,
# quote and backslash.
_SCOPE_TOKEN = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]+$")

_EC_CURVES = {
    "ES256": ec.SECP256R1,
    "ES384": ec.SECP384R1,
    "ES512": ec.SECP521R1,
}


def _validate_private_key(pem: str, alg: str) -> None:
    """Reject unusable key material and alg/key mismatches at save time."""
    if alg not in OAUTH_PRIVATE_KEY_ALGS:
        raise OAuthConfigValidationError(f"Unsupported private_key_alg: {alg}")
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except TypeError as exc:
        raise OAuthConfigValidationError(
            "private key is password-protected; supply an unencrypted PEM"
        ) from exc
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise OAuthConfigValidationError(
            "private key is not a valid PEM private key"
        ) from exc

    if alg.startswith("RS"):
        if not isinstance(key, RSAPrivateKey):
            raise OAuthConfigValidationError(f"{alg} requires an RSA private key")
        return
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise OAuthConfigValidationError(f"{alg} requires an EC private key")
    expected_curve = _EC_CURVES[alg]
    if not isinstance(key.curve, expected_curve):
        raise OAuthConfigValidationError(
            f"{alg} requires a {expected_curve.name} key"
        )


def _validate_scopes(provider: OAuthSSOProvider, scopes: list[str] | None) -> list[str]:
    """Normalize scopes, keeping provider defaults and requiring openid."""
    if scopes is None:
        return list(_DEFAULT_SCOPES[provider])
    cleaned: list[str] = []
    for scope in scopes:
        value = scope.strip()
        if not value:
            raise OAuthConfigValidationError("scopes must not contain empty values")
        if not _SCOPE_TOKEN.fullmatch(value):
            raise OAuthConfigValidationError(
                "scopes must be space-delimited OAuth scope tokens"
            )
        if value not in cleaned:
            cleaned.append(value)
    if "openid" not in cleaned:
        raise OAuthConfigValidationError(
            "scopes must include 'openid' so the provider returns an id_token"
        )
    if provider == "microsoft" and not any(
        scope in {"User.Read", "https://graph.microsoft.com/User.Read"}
        for scope in cleaned
    ):
        raise OAuthConfigValidationError(
            "Microsoft scopes must include 'User.Read' so Bifrost can read "
            "the signed-in user's profile from Microsoft Graph"
        )
    return cleaned


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
                logger.error(f"Failed to decrypt secret {key}: {e}")
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

    async def get_login_preference(self) -> OAuthLoginPreference:
        """Return the platform-wide preferred SSO behavior."""
        result = await self.db.execute(
            select(SystemConfig).where(
                SystemConfig.category == OAUTH_CONFIG_CATEGORY,
                SystemConfig.key == OAUTH_LOGIN_PREFERENCE,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalar_one_or_none()
        if not config or not config.value_json:
            return OAuthLoginPreference()
        return OAuthLoginPreference.model_validate(config.value_json)

    async def get_login_preference_for_settings(self) -> OAuthLoginPreference:
        """Resolve the safe legacy admin default into editable user exclusions."""

        preference = await self.get_login_preference()
        if preference.excluded_user_ids is None:
            preference.excluded_user_ids = list(
                (
                    await self.db.execute(
                        select(User.id).where(
                            User.is_superuser.is_(True), User.identity_kind.is_(None)
                        )
                    )
                ).scalars()
            )
        return preference

    async def _is_login_method_allowed_for(
        self,
        preference: OAuthLoginPreference,
        enabled: bool,
        user: User | None = None,
        *,
        registration_organization_id: UUID | None = None,
    ) -> bool:
        """Evaluate the shared policy against a resolved account, never caller claims."""
        if enabled:
            return True
        if user is not None:
            excluded = preference.excluded_user_ids
            if (excluded is None and user.is_superuser) or (
                excluded is not None and user.id in excluded
            ):
                return True
        organization_id = (
            user.organization_id if user is not None else registration_organization_id
        )
        if organization_id in preference.excluded_organization_ids:
            return True
        from src.repositories.users import UserRepository

        return not await UserRepository(self.db).has_any_users()

    async def is_password_allowed_for(self, user: User | None = None) -> bool:
        preference = await self.get_login_preference()
        return await self._is_login_method_allowed_for(
            preference, preference.password_login_enabled, user
        )

    async def is_password_registration_allowed(self, email: str) -> bool:
        from src.repositories.users import UserRepository
        from src.repositories.organizations import OrganizationRepository

        user = await UserRepository(self.db).get_by_email(email)
        if user is None:
            org = await OrganizationRepository(self.db).get_by_domain(
                email.split("@")[1].lower()
            )
            preference = await self.get_login_preference()
            return await self._is_login_method_allowed_for(
                preference,
                preference.password_login_enabled,
                registration_organization_id=org.id if org else None,
            )
        return await self.is_password_allowed_for(user)

    async def is_passkey_allowed_for(self, user: User | None = None) -> bool:
        preference = await self.get_login_preference()
        return await self._is_login_method_allowed_for(
            preference, preference.passkey_login_enabled, user
        )

    async def set_login_preference(
        self,
        preference: OAuthLoginPreference,
        updated_by: str = "system",
    ) -> OAuthLoginPreference:
        """Persist a preferred provider after verifying it is configured."""
        from shared.sso_policy import validate_recovery_admin

        await validate_recovery_admin(self.db, preference)

        if preference.default_sso_provider is not None:
            provider_config = await self.get_provider_config(
                preference.default_sso_provider
            )
            if not provider_config or not provider_config.is_complete:
                raise OAuthLoginPreferenceError(
                    f"{preference.default_sso_provider.title()} SSO must be configured "
                    "before it can be preferred"
                )

        if (
            not preference.password_login_enabled
            or not preference.passkey_login_enabled
        ) and not await self.get_available_providers():
            raise OAuthLoginPreferenceError(
                "An SSO provider must be configured before disabling "
                "password or passkey login"
            )

        result = await self.db.execute(
            select(SystemConfig).where(
                SystemConfig.category == OAUTH_CONFIG_CATEGORY,
                SystemConfig.key == OAUTH_LOGIN_PREFERENCE,
                SystemConfig.organization_id.is_(None),
            )
        )
        existing = result.scalar_one_or_none()
        value_json = preference.model_dump(mode="json")

        if existing:
            existing.value_json = value_json
            existing.updated_by = updated_by
        else:
            self.db.add(
                SystemConfig(
                    category=OAUTH_CONFIG_CATEGORY,
                    key=OAUTH_LOGIN_PREFERENCE,
                    value_json=value_json,
                    organization_id=None,
                    created_by=updated_by,
                    updated_by=updated_by,
                )
            )

        await self.db.flush()
        return preference

    # =========================================================================
    # Provider Configuration CRUD
    # =========================================================================

    async def _get_scopes(self, provider: OAuthSSOProvider) -> list[str]:
        raw = await self._get_config_value(_PROVIDER_SCOPES_KEY[provider])
        if raw:
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                value = None
            if isinstance(value, list) and value:
                return [str(scope) for scope in value]
        return list(_DEFAULT_SCOPES[provider])

    async def _get_client_auth_settings(
        self, provider: OAuthSSOProvider
    ) -> tuple[str, str | None, str | None, str | None]:
        """Return (method, private key, kid, alg) for a provider."""
        method_key, key_key, kid_key, alg_key = _PROVIDER_AUTH_KEYS[provider]
        method = await self._get_config_value(method_key)
        private_key = await self._get_secret_value(key_key)
        kid = await self._get_config_value(kid_key)
        alg = await self._get_config_value(alg_key)
        return (
            method or "client_secret_post",
            private_key,
            kid or None,
            alg or "RS256",
        )

    async def _validate_provider_secret(
        self, provider: OAuthSSOProvider, config: Any
    ) -> str | None:
        """Return the secret to store, or None to leave the stored one as-is."""
        if config.client_secret:
            return config.client_secret.strip() or None
        existing = await self._get_secret_value(_PROVIDER_SECRET_KEYS[provider])
        if config.token_endpoint_auth_method in {
            "client_secret_post",
            "client_secret_basic",
        } and not existing:
            raise OAuthConfigValidationError(
                "client_secret is required for client_secret_post/client_secret_basic"
            )
        return None

    async def _validate_and_extract_client_auth(
        self, provider: OAuthSSOProvider, config: Any
    ) -> tuple[str, str | None, str | None, str, bool, bool]:
        """Validate the requested method/key at save time, before any login.

        Returns (method, key_to_store, kid, alg, clear_key, key_replaced).
        ``kid`` is only meaningful when the key is replaced.
        """
        method = config.token_endpoint_auth_method
        if method not in _TOKEN_ENDPOINT_AUTH_METHODS:
            raise OAuthConfigValidationError(
                f"Unsupported token_endpoint_auth_method: {method}"
            )
        alg = config.private_key_alg or "RS256"
        if alg not in OAUTH_PRIVATE_KEY_ALGS:
            raise OAuthConfigValidationError(f"Unsupported private_key_alg: {alg}")

        provided_key = config.private_key.strip() if config.private_key else None
        clear_key = bool(config.clear_private_key)
        if clear_key and provided_key:
            raise OAuthConfigValidationError(
                "Use either clear_private_key or private_key, not both"
            )

        _, existing_key, existing_kid, _ = await self._get_client_auth_settings(provider)
        effective_key = None if clear_key else (provided_key or existing_key)
        if effective_key:
            _validate_private_key(effective_key, alg)
        if method == "private_key_jwt" and not effective_key:
            raise OAuthConfigValidationError(
                "private_key_jwt requires a private key (PEM)"
            )

        key_replaced = bool(provided_key)
        kid = (config.private_key_kid or None) if key_replaced else existing_kid
        return method, provided_key, kid, alg, clear_key, key_replaced

    async def _store_client_auth(
        self,
        provider: OAuthSSOProvider,
        method: str,
        private_key: str | None,
        kid: str | None,
        alg: str,
        clear_key: bool,
        key_replaced: bool,
        updated_by: str,
    ) -> None:
        method_key, key_key, kid_key, alg_key = _PROVIDER_AUTH_KEYS[provider]
        await self._set_config_value(method_key, method, updated_by=updated_by)
        await self._set_config_value(alg_key, alg, updated_by=updated_by)

        if clear_key:
            # Explicit revocation: remove the key and its kid, keep the provider.
            await self._delete_config_keys([key_key, kid_key])
        elif private_key and key_replaced:
            await self._set_config_value(
                key_key, private_key, is_secret=True, updated_by=updated_by
            )
            if kid:
                await self._set_config_value(kid_key, kid, updated_by=updated_by)
            else:
                await self._delete_config_keys([kid_key])
        # Omitted private_key leaves both the stored key and kid untouched.

    async def get_provider_config(
        self, provider: OAuthSSOProvider
    ) -> OAuthProviderConfig | None:
        """
        Get OAuth configuration for a provider.

        Returns None if the provider is not configured.
        """
        scopes = await self._get_scopes(provider)
        if provider == "microsoft":
            client_id = await self._get_config_value(OAUTH_MICROSOFT_CLIENT_ID)
            client_secret = await self._get_secret_value(OAUTH_MICROSOFT_CLIENT_SECRET)
            tenant_id = await self._get_config_value(OAUTH_MICROSOFT_TENANT_ID)
            method, private_key, kid, alg = await self._get_client_auth_settings(provider)

            if not client_id:
                return None
            if method == "private_key_jwt" and not private_key:
                return None
            if method != "private_key_jwt" and not client_secret:
                return None

            return OAuthProviderConfig(
                provider="microsoft",
                client_id=client_id,
                client_secret=client_secret or "",
                tenant_id=tenant_id or "common",
                token_endpoint_auth_method=method,
                private_key=private_key,
                private_key_kid=kid,
                private_key_alg=alg,
                scopes=scopes,
            )

        elif provider == "google":
            client_id = await self._get_config_value(OAUTH_GOOGLE_CLIENT_ID)
            client_secret = await self._get_secret_value(OAUTH_GOOGLE_CLIENT_SECRET)
            method, private_key, kid, alg = await self._get_client_auth_settings(provider)

            if not client_id:
                return None
            if method == "private_key_jwt" and not private_key:
                return None
            if method != "private_key_jwt" and not client_secret:
                return None

            return OAuthProviderConfig(
                provider="google",
                client_id=client_id,
                client_secret=client_secret or "",
                token_endpoint_auth_method=method,
                private_key=private_key,
                private_key_kid=kid,
                private_key_alg=alg,
                scopes=scopes,
            )

        elif provider == "oidc":
            discovery_url = await self._get_config_value(OAUTH_OIDC_DISCOVERY_URL)
            client_id = await self._get_config_value(OAUTH_OIDC_CLIENT_ID)
            client_secret = await self._get_secret_value(OAUTH_OIDC_CLIENT_SECRET)
            display_name = await self._get_config_value(OAUTH_OIDC_DISPLAY_NAME)
            method, private_key, kid, alg = await self._get_client_auth_settings(provider)

            if not discovery_url or not client_id:
                return None
            if method == "private_key_jwt" and not private_key:
                return None
            if method != "private_key_jwt" and not client_secret:
                return None

            return OAuthProviderConfig(
                provider="oidc",
                client_id=client_id,
                client_secret=client_secret or "",
                discovery_url=discovery_url,
                display_name=display_name or "SSO",
                token_endpoint_auth_method=method,
                private_key=private_key,
                private_key_kid=kid,
                private_key_alg=alg,
                scopes=scopes,
            )

        return None

    async def set_microsoft_config(
        self,
        config: MicrosoftOAuthConfigRequest,
        updated_by: str = "system",
    ) -> None:
        """Set Microsoft OAuth configuration."""
        auth = await self._validate_and_extract_client_auth("microsoft", config)
        secret = await self._validate_provider_secret("microsoft", config)
        await self._set_config_value(
            OAUTH_MICROSOFT_CLIENT_ID, config.client_id, updated_by=updated_by
        )
        if secret:
            await self._set_config_value(
                OAUTH_MICROSOFT_CLIENT_SECRET,
                secret,
                is_secret=True,
                updated_by=updated_by,
            )
        await self._set_config_value(
            OAUTH_MICROSOFT_TENANT_ID, config.tenant_id, updated_by=updated_by
        )
        await self._set_config_value(
            _PROVIDER_SCOPES_KEY["microsoft"],
            json.dumps(_validate_scopes("microsoft", config.scopes)),
            updated_by=updated_by,
        )
        await self._store_client_auth("microsoft", *auth, updated_by)
        logger.info(f"Microsoft OAuth config updated by {updated_by}")

    async def set_google_config(
        self,
        config: GoogleOAuthConfigRequest,
        updated_by: str = "system",
    ) -> None:
        """Set Google OAuth configuration."""
        auth = await self._validate_and_extract_client_auth("google", config)
        secret = await self._validate_provider_secret("google", config)
        await self._set_config_value(
            OAUTH_GOOGLE_CLIENT_ID, config.client_id, updated_by=updated_by
        )
        if secret:
            await self._set_config_value(
                OAUTH_GOOGLE_CLIENT_SECRET,
                secret,
                is_secret=True,
                updated_by=updated_by,
            )
        await self._set_config_value(
            _PROVIDER_SCOPES_KEY["google"],
            json.dumps(_validate_scopes("google", config.scopes)),
            updated_by=updated_by,
        )
        await self._store_client_auth("google", *auth, updated_by)
        logger.info(f"Google OAuth config updated by {updated_by}")

    async def set_oidc_config(
        self,
        config: OIDCConfigRequest,
        updated_by: str = "system",
    ) -> None:
        """Set OIDC provider configuration."""
        auth = await self._validate_and_extract_client_auth("oidc", config)
        secret = await self._validate_provider_secret("oidc", config)
        await self._set_config_value(
            OAUTH_OIDC_DISCOVERY_URL, config.discovery_url, updated_by=updated_by
        )
        await self._set_config_value(
            OAUTH_OIDC_CLIENT_ID, config.client_id, updated_by=updated_by
        )
        if secret:
            await self._set_config_value(
                OAUTH_OIDC_CLIENT_SECRET,
                secret,
                is_secret=True,
                updated_by=updated_by,
            )
        await self._set_config_value(
            OAUTH_OIDC_DISPLAY_NAME, config.display_name, updated_by=updated_by
        )
        await self._set_config_value(
            _PROVIDER_SCOPES_KEY["oidc"],
            json.dumps(_validate_scopes("oidc", config.scopes)),
            updated_by=updated_by,
        )
        await self._store_client_auth("oidc", *auth, updated_by)
        logger.info(f"OIDC config updated by {updated_by}")

    async def delete_provider_config(
        self,
        provider: OAuthSSOProvider,
        updated_by: str = "system",
    ) -> bool:
        """Delete all configuration for a provider."""
        from shared.sso_policy import lock_login_policy

        await lock_login_policy(self.db)
        preference = await self.get_login_preference()
        if (
            preference.auto_redirect_to_sso
            and preference.default_sso_provider == provider
        ):
            raise OAuthProviderPreferenceConflict(
                f"Disable preferred SSO redirect before removing {provider.title()}"
            )
        if (
            not preference.password_login_enabled
            or not preference.passkey_login_enabled
        ):
            available = await self.get_available_providers()
            if provider in available and len(available) == 1:
                raise OAuthProviderPreferenceConflict(
                    f"Re-enable password and passkey login before removing "
                    f"{provider.title()} (last SSO provider)"
                )

        if provider == "microsoft":
            keys = [
                OAUTH_MICROSOFT_CLIENT_ID,
                OAUTH_MICROSOFT_CLIENT_SECRET,
                OAUTH_MICROSOFT_TENANT_ID,
                *_PROVIDER_AUTH_KEYS["microsoft"],
                OAUTH_MICROSOFT_SCOPES,
            ]
        elif provider == "google":
            keys = [
                OAUTH_GOOGLE_CLIENT_ID,
                OAUTH_GOOGLE_CLIENT_SECRET,
                *_PROVIDER_AUTH_KEYS["google"],
                OAUTH_GOOGLE_SCOPES,
            ]
        elif provider == "oidc":
            keys = [
                OAUTH_OIDC_DISCOVERY_URL,
                OAUTH_OIDC_CLIENT_ID,
                OAUTH_OIDC_CLIENT_SECRET,
                OAUTH_OIDC_DISPLAY_NAME,
                *_PROVIDER_AUTH_KEYS["oidc"],
                OAUTH_OIDC_SCOPES,
            ]
        else:
            return False

        await self._delete_config_keys(keys)
        if preference.default_sso_provider == provider:
            await self.set_login_preference(
                OAuthLoginPreference(
                    excluded_user_ids=preference.excluded_user_ids,
                    excluded_organization_ids=preference.excluded_organization_ids,
                    password_login_enabled=preference.password_login_enabled,
                    passkey_login_enabled=preference.passkey_login_enabled,
                ),
                updated_by=updated_by,
            )
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
                        token_endpoint_auth_method=config.token_endpoint_auth_method,  # type: ignore[arg-type]
                        private_key_set=bool(config.private_key),
                        private_key_kid=config.private_key_kid,
                        private_key_alg=config.private_key_alg,
                        scopes=config.scopes or [],
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

    async def test_client_auth_configuration(
        self, provider: OAuthSSOProvider, test_data: dict[str, Any] | None = None
    ) -> str | None:
        """Exercise the configured client auth locally; no network call.

        Returns an error message, or None when the credential can be built.
        """
        from src.services.oauth_sso import _token_request_auth

        test_data = test_data or {}
        saved = await self.get_provider_config(provider)
        config = {
            "client_id": test_data.get("client_id")
            or (saved.client_id if saved else None),
            "client_secret": test_data.get("client_secret")
            or (saved.client_secret if saved else None),
            "token_endpoint_auth_method": (
                saved.token_endpoint_auth_method if saved else "client_secret_post"
            ),
            "private_key": saved.private_key if saved else None,
            "private_key_kid": saved.private_key_kid if saved else None,
            "private_key_alg": saved.private_key_alg if saved else "RS256",
        }
        if not config["client_id"]:
            return "client_id is required before testing client authentication"
        try:
            _token_request_auth(
                config,
                "https://example.invalid/token",
                {
                    "grant_type": "authorization_code",
                    "code": "test",
                    "redirect_uri": "https://example.invalid/callback",
                    "code_verifier": "test",
                },
            )
        except Exception as exc:
            return f"Client authentication is not usable: {exc}"
        return None

    async def test_provider_config(
        self,
        provider: OAuthSSOProvider,
        test_data: dict[str, Any] | None = None,
    ) -> OAuthConfigTestResponse:
        """Test discovery for a provider, then validate its client auth."""
        test_data = test_data or {}

        if provider == "microsoft":
            result = await self.test_microsoft_config(
                client_id=test_data.get("client_id"),
                client_secret=test_data.get("client_secret"),
                tenant_id=test_data.get("tenant_id"),
            )
        elif provider == "google":
            result = await self.test_google_config(
                client_id=test_data.get("client_id"),
                client_secret=test_data.get("client_secret"),
            )
        elif provider == "oidc":
            result = await self.test_oidc_config(
                discovery_url=test_data.get("discovery_url"),
                client_id=test_data.get("client_id"),
                client_secret=test_data.get("client_secret"),
            )
        else:
            return OAuthConfigTestResponse(
                success=False,
                message=f"Unknown provider: {provider}",
            )

        if result.success:
            client_auth_error = await self.test_client_auth_configuration(
                provider, test_data
            )
            if client_auth_error:
                return OAuthConfigTestResponse(
                    success=False,
                    message=client_auth_error,
                    details=result.details,
                )
        return result
