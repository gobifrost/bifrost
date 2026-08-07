"""
Passkey Service - WebAuthn/Passkey authentication business logic.

Handles passkey registration, authentication, and credential management
using the py_webauthn library for WebAuthn protocol compliance.
"""

import json
import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    generate_user_handle,
    parse_authentication_credential_json,
    parse_registration_credential_json,
)
from webauthn.helpers.structs import (
    AuthenticatorTransport,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from src.config import get_settings
from src.core.cache import get_shared_redis
from src.core.constants import PROVIDER_ORG_ID
from src.models import User
from src.models.orm.mfa import UserPasskey

# Redis key prefixes and TTLs
PASSKEY_REG_CHALLENGE_PREFIX = "passkey_reg_challenge:"
PASSKEY_AUTH_CHALLENGE_PREFIX = "passkey_auth_challenge:"
PASSKEY_SETUP_TOKEN_PREFIX = "passkey_setup:"
CHALLENGE_TTL_SECONDS = 300  # 5 minutes


def _normalize_transports(transports: list | None) -> list[AuthenticatorTransport] | None:
    """Convert stored JSONB transport strings into py_webauthn enum values."""
    if not transports:
        return None

    normalized: list[AuthenticatorTransport] = []
    for transport in transports:
        if isinstance(transport, AuthenticatorTransport):
            normalized.append(transport)
            continue
        if not isinstance(transport, str) or not transport:
            continue
        try:
            normalized.append(AuthenticatorTransport(transport))
        except ValueError:
            continue

    return normalized or None


class PasskeyService:
    """Service for WebAuthn passkey operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    # ========================================================================
    # Registration
    # ========================================================================

    async def generate_registration_options(
        self,
        user_id: UUID,
    ) -> dict:
        """
        Generate WebAuthn registration options for passkey enrollment.

        Creates a challenge and returns options that the browser uses to
        create a new passkey credential.

        Args:
            user_id: ID of the user registering a passkey

        Returns:
            Dictionary with registration options (JSON-serializable)

        Raises:
            ValueError: If user not found
        """
        # Fetch the user from the database
        user = await self._get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        # Get or create WebAuthn user handle
        if not user.webauthn_user_id:
            user.webauthn_user_id = generate_user_handle()
            await self.db.flush()

        # Get existing credentials to exclude (prevent duplicate registrations)
        existing_passkeys = await self._get_user_passkeys(user.id)
        exclude_credentials = [
            PublicKeyCredentialDescriptor(
                id=passkey.credential_id,
                transports=_normalize_transports(passkey.transports),
            )
            for passkey in existing_passkeys
        ]

        # Generate registration options
        options = generate_registration_options(
            rp_id=self.settings.webauthn_rp_id,
            rp_name=self.settings.webauthn_rp_name,
            user_id=user.webauthn_user_id,
            user_name=user.email,
            user_display_name=user.name or user.email,
            exclude_credentials=exclude_credentials if exclude_credentials else None,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )

        # Store challenge in Redis for verification
        redis = await get_shared_redis()
        challenge_key = f"{PASSKEY_REG_CHALLENGE_PREFIX}{user.id}"
        await redis.setex(
            challenge_key,
            CHALLENGE_TTL_SECONDS,
            bytes_to_base64url(options.challenge),
        )

        # Return as JSON-serializable dict
        return json.loads(options_to_json(options))

    async def verify_registration(
        self,
        user_id: UUID,
        credential_json: str,
        device_name: str | None = None,
    ) -> UserPasskey:
        """
        Verify passkey registration and store the credential.

        Args:
            user_id: ID of the user completing registration
            credential_json: JSON string of the registration response from browser
            device_name: Optional friendly name for the passkey

        Returns:
            Created UserPasskey record

        Raises:
            ValueError: If verification fails or challenge is invalid
        """
        # Fetch the user from the database
        user = await self._get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        # Get and delete challenge from Redis (single-use)
        redis = await get_shared_redis()
        challenge_key = f"{PASSKEY_REG_CHALLENGE_PREFIX}{user_id}"
        challenge_b64 = await redis.get(challenge_key)
        if not challenge_b64:
            raise ValueError("Registration challenge not found or expired")
        await redis.delete(challenge_key)

        expected_challenge = base64url_to_bytes(challenge_b64)

        # Parse the credential JSON
        credential = parse_registration_credential_json(credential_json)

        # Verify the registration response
        try:
            verification = verify_registration_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_origin=self.settings.webauthn_origins,
                expected_rp_id=self.settings.webauthn_rp_id,
            )
        except Exception as e:
            raise ValueError(f"Registration verification failed: {e}") from e

        # Extract transports from credential response if available
        transports = []
        if hasattr(credential.response, 'transports') and credential.response.transports:
            transports = list(credential.response.transports)

        # Create passkey record
        passkey = UserPasskey(
            user_id=user.id,
            credential_id=verification.credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            transports=transports,
            device_type=verification.credential_device_type,
            backed_up=verification.credential_backed_up,
            name=device_name or "Passkey",
        )
        self.db.add(passkey)
        await self.db.flush()

        return passkey

    # ========================================================================
    # Authentication
    # ========================================================================

    async def generate_authentication_options(
        self,
        email: str | None = None,
    ) -> tuple[str, dict]:
        """
        Generate WebAuthn authentication options.

        Args:
            email: Optional email to target specific user's credentials.
                   If None, uses discoverable credentials (resident keys).

        Returns:
            Tuple of (challenge_id, options_dict)
        """
        allow_credentials = None

        if email:
            # Get user's passkeys for targeted authentication
            user = await self._get_user_by_email(email)
            if user:
                passkeys = await self._get_user_passkeys(user.id)
                if passkeys:
                    allow_credentials = [
                        PublicKeyCredentialDescriptor(
                            id=passkey.credential_id,
                            transports=_normalize_transports(passkey.transports),
                        )
                        for passkey in passkeys
                    ]

        # Generate authentication options
        options = generate_authentication_options(
            rp_id=self.settings.webauthn_rp_id,
            allow_credentials=allow_credentials,
            user_verification=UserVerificationRequirement.REQUIRED,
        )

        # Store challenge in Redis with unique ID
        challenge_id = secrets.token_urlsafe(32)
        redis = await get_shared_redis()
        challenge_key = f"{PASSKEY_AUTH_CHALLENGE_PREFIX}{challenge_id}"
        await redis.setex(
            challenge_key,
            CHALLENGE_TTL_SECONDS,
            bytes_to_base64url(options.challenge),
        )

        return challenge_id, json.loads(options_to_json(options))

    async def verify_authentication(
        self,
        challenge_id: str,
        credential_json: str,
    ) -> User:
        """
        Verify passkey authentication and return the authenticated user.

        Args:
            challenge_id: Challenge ID from generate_authentication_options
            credential_json: JSON string of the authentication response from browser

        Returns:
            Authenticated User

        Raises:
            ValueError: If verification fails
        """
        # Get and delete challenge from Redis (single-use)
        redis = await get_shared_redis()
        challenge_key = f"{PASSKEY_AUTH_CHALLENGE_PREFIX}{challenge_id}"
        challenge_b64 = await redis.get(challenge_key)
        if not challenge_b64:
            raise ValueError("Authentication challenge not found or expired")
        await redis.delete(challenge_key)

        expected_challenge = base64url_to_bytes(challenge_b64)

        # Parse the credential JSON
        credential = parse_authentication_credential_json(credential_json)

        # Find the passkey by credential ID
        passkey = await self._get_passkey_by_credential_id(credential.raw_id)
        if not passkey:
            raise ValueError("Unknown credential")

        # Verify the authentication response
        try:
            verification = verify_authentication_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_origin=self.settings.webauthn_origins,
                expected_rp_id=self.settings.webauthn_rp_id,
                credential_public_key=passkey.public_key,
                credential_current_sign_count=passkey.sign_count,
            )
        except Exception as e:
            raise ValueError(f"Authentication verification failed: {e}") from e

        # Update sign count and last used (prevents replay attacks)
        passkey.sign_count = verification.new_sign_count
        passkey.last_used_at = datetime.now(timezone.utc)
        await self.db.flush()

        # Get and return the user
        user = await self._get_user_by_id(passkey.user_id)
        if not user:
            raise ValueError("User not found")
        if not user.is_active:
            raise ValueError("User account is inactive")

        return user

    # ========================================================================
    # Passkey Management
    # ========================================================================

    async def list_passkeys(self, user_id: UUID) -> list[UserPasskey]:
        """
        List all passkeys for a user.

        Args:
            user_id: User ID

        Returns:
            List of UserPasskey records
        """
        return await self._get_user_passkeys(user_id)

    async def delete_passkey(self, user_id: UUID, passkey_id: UUID) -> bool:
        """
        Delete a passkey.

        Args:
            user_id: User ID (for authorization check)
            passkey_id: Passkey ID to delete

        Returns:
            True if deleted, False if not found

        Raises:
            ValueError: If passkey doesn't belong to user
        """
        result = await self.db.execute(
            select(UserPasskey).where(UserPasskey.id == passkey_id)
        )
        passkey = result.scalar_one_or_none()

        if not passkey:
            return False

        if passkey.user_id != user_id:
            raise ValueError("Passkey does not belong to user")

        await self.db.delete(passkey)
        await self.db.flush()
        return True

    async def get_passkey_count(self, user_id: UUID) -> int:
        """
        Get the number of passkeys for a user.

        Args:
            user_id: User ID

        Returns:
            Number of passkeys
        """
        result = await self.db.execute(
            select(UserPasskey).where(UserPasskey.user_id == user_id)
        )
        return len(result.scalars().all())

    # ========================================================================
    # Private Helpers
    # ========================================================================

    async def _get_user_passkeys(self, user_id: UUID) -> list[UserPasskey]:
        """Get all passkeys for a user."""
        result = await self.db.execute(
            select(UserPasskey)
            .where(UserPasskey.user_id == user_id)
            .order_by(UserPasskey.created_at.desc())
        )
        return list(result.scalars().all())

    async def _get_passkey_by_credential_id(
        self, credential_id: bytes
    ) -> UserPasskey | None:
        """Get a passkey by its credential ID."""
        result = await self.db.execute(
            select(UserPasskey)
            .where(UserPasskey.credential_id == credential_id)
            .options(selectinload(UserPasskey.user))
        )
        return result.scalar_one_or_none()

    async def _get_user_by_email(self, email: str) -> User | None:
        """Get a user by email."""
        result = await self.db.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def _get_user_by_id(self, user_id: UUID) -> User | None:
        """Get a user by ID."""
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    # ========================================================================
    # First-Time Setup with Passkey (Passwordless Registration)
    # ========================================================================

    async def generate_setup_registration_options(
        self,
        email: str,
        name: str | None = None,
    ) -> tuple[str, dict]:
        """
        Generate WebAuthn options for first-time platform setup (passwordless).

        This creates a registration token that stores the pending user info,
        and returns WebAuthn options for the browser to create a passkey.

        Args:
            email: Email address for the new admin account
            name: Optional display name

        Returns:
            Tuple of (registration_token, options_dict)

        Raises:
            ValueError: If users already exist (not first-time setup) or email in use
        """
        from src.repositories.users import UserRepository

        user_repo = UserRepository(self.db)

        # Validate first-user scenario
        if await user_repo.has_any_users():
            raise ValueError("Passkey setup registration is only available during first-time platform setup")

        # Check email not already registered (race condition protection)
        existing = await self._get_user_by_email(email)
        if existing:
            raise ValueError("Email already registered")

        # Generate a temporary WebAuthn user handle
        temp_user_id = generate_user_handle()

        # Generate registration options
        options = generate_registration_options(
            rp_id=self.settings.webauthn_rp_id,
            rp_name=self.settings.webauthn_rp_name,
            user_id=temp_user_id,
            user_name=email,
            user_display_name=name or email.split("@")[0],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )

        # Create registration token
        registration_token = secrets.token_urlsafe(32)

        # Store pending registration data in Redis
        redis = await get_shared_redis()
        setup_data = {
            "email": email,
            "name": name or email.split("@")[0],
            "webauthn_user_id": bytes_to_base64url(temp_user_id),
            "challenge": bytes_to_base64url(options.challenge),
        }
        await redis.setex(
            f"{PASSKEY_SETUP_TOKEN_PREFIX}{registration_token}",
            CHALLENGE_TTL_SECONDS,
            json.dumps(setup_data),
        )

        return registration_token, json.loads(options_to_json(options))

    async def verify_setup_registration(
        self,
        registration_token: str,
        credential_json: str,
        device_name: str | None = None,
    ) -> tuple[User, UserPasskey]:
        """
        Verify setup registration and create user + passkey atomically.

        Args:
            registration_token: Token from generate_setup_registration_options
            credential_json: JSON string of the registration response from browser
            device_name: Optional friendly name for the passkey

        Returns:
            Tuple of (created_user, created_passkey)

        Raises:
            ValueError: If token invalid, expired, or verification fails
        """
        from src.repositories.users import UserRepository

        # Get and delete setup data from Redis (single-use)
        redis = await get_shared_redis()
        setup_key = f"{PASSKEY_SETUP_TOKEN_PREFIX}{registration_token}"
        setup_data_json = await redis.get(setup_key)
        if not setup_data_json:
            raise ValueError("Registration token not found or expired")
        await redis.delete(setup_key)

        setup_data = json.loads(setup_data_json)
        expected_challenge = base64url_to_bytes(setup_data["challenge"])

        # Parse the credential JSON
        credential = parse_registration_credential_json(credential_json)

        # Verify the registration response
        try:
            verification = verify_registration_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_origin=self.settings.webauthn_origins,
                expected_rp_id=self.settings.webauthn_rp_id,
            )
        except Exception as e:
            raise ValueError(f"Registration verification failed: {e}") from e

        # Double-check first-user scenario (race condition protection)
        user_repo = UserRepository(self.db)
        if await user_repo.has_any_users():
            raise ValueError("Another user was created during registration")

        # Create user as Platform admin (first user)
        # is_superuser=True with org_id = platform admin in that org
        user = User(
            email=setup_data["email"],
            name=setup_data["name"],
            is_active=True,
            is_superuser=True,
            is_registered=True,
            hashed_password=None,  # Passwordless user
            webauthn_user_id=base64url_to_bytes(setup_data["webauthn_user_id"]),
            organization_id=PROVIDER_ORG_ID,
        )
        self.db.add(user)
        await self.db.flush()  # Get user.id

        # Extract transports from credential response if available
        transports = []
        if hasattr(credential.response, 'transports') and credential.response.transports:
            transports = list(credential.response.transports)

        # Create passkey
        passkey = UserPasskey(
            user_id=user.id,
            credential_id=verification.credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            transports=transports,
            device_type=verification.credential_device_type,
            backed_up=verification.credential_backed_up,
            name=device_name or "Setup Passkey",
        )
        self.db.add(passkey)
        await self.db.flush()

        return user, passkey
