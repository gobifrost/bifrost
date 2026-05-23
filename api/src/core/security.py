"""
Security Utilities

Password hashing and JWT token handling using industry-standard libraries.
Based on FastAPI's official security tutorial patterns.

Uses pwdlib (modern replacement for unmaintained passlib) for password hashing.
"""

import base64
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from src.config import get_settings

# Password hashing using pwdlib with bcrypt
# This is the modern replacement for passlib, recommended by FastAPI
# We explicitly use BcryptHasher to avoid requiring argon2 dependency
password_hash = PasswordHash((BcryptHasher(),))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a hashed password.

    Args:
        plain_password: The password to verify
        hashed_password: The hashed password to compare against

    Returns:
        True if password matches, False otherwise
    """
    return password_hash.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Hash a password using bcrypt.

    Args:
        password: Plain text password to hash

    Returns:
        Hashed password string
    """
    return password_hash.hash(password)


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None
) -> str:
    """
    Create a JWT access token.

    Args:
        data: Dictionary of claims to encode in the token
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string
    """
    settings = get_settings()

    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )

    to_encode.update({
        "exp": expire,
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    })

    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.algorithm
    )

    return encoded_jwt


def create_refresh_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None
) -> tuple[str, str]:
    """
    Create a JWT refresh token with JTI for revocation support.

    Refresh tokens have longer expiration and are used to obtain new access tokens.
    Each token has a unique JTI (JWT ID) that must be stored in Redis for validation.

    Args:
        data: Dictionary of claims to encode in the token
        expires_delta: Optional custom expiration time

    Returns:
        Tuple of (encoded JWT token string, JTI for Redis storage)
    """
    import uuid

    settings = get_settings()

    jti = str(uuid.uuid4())
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.refresh_token_expire_days
        )

    to_encode.update({
        "exp": expire,
        "type": "refresh",
        "jti": jti,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    })

    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.algorithm
    )

    return encoded_jwt, jti


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any] | None:
    """
    Decode and validate a JWT token.

    Args:
        token: JWT token string to decode
        expected_type: If provided, validates that token type matches (e.g., "access", "refresh")

    Returns:
        Decoded token payload or None if invalid/expired/wrong type
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )

        # Validate token type if specified
        if expected_type is not None and payload.get("type") != expected_type:
            return None

        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def create_mfa_token(user_id: str, purpose: str = "mfa_verify") -> str:
    """
    Create a short-lived token for MFA verification step.

    This token is returned after password verification and must be
    provided along with the MFA code to complete login.

    Args:
        user_id: User ID
        purpose: Token purpose (mfa_verify, mfa_setup)

    Returns:
        Encoded JWT token string
    """
    settings = get_settings()

    # Use different expiry times based on purpose
    # Setup needs more time for users to install/configure authenticator apps
    if purpose == "mfa_setup":
        expire_minutes = settings.mfa_setup_token_expire_minutes
    else:
        expire_minutes = settings.mfa_verify_token_expire_minutes

    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)

    to_encode = {
        "sub": user_id,
        "type": purpose,
        "exp": expire,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }

    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_mfa_token(token: str, expected_purpose: str = "mfa_verify") -> dict[str, Any] | None:
    """
    Decode and validate an MFA token.

    Args:
        token: JWT token string to decode
        expected_purpose: Expected token purpose

    Returns:
        Decoded token payload or None if invalid/expired/wrong type
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
        if payload.get("type") != expected_purpose:
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def create_embed_token(
    app_id: str,
    org_id: str | None,
    verified_params: dict[str, str],
) -> str:
    """Create an 8-hour JWT for embed iframe sessions.

    Args:
        app_id: Application UUID string.
        org_id: Organization UUID string (from the app).
        verified_params: HMAC-verified query parameters.

    Returns:
        Encoded JWT string with type="embed".
    """
    from src.core.constants import SYSTEM_USER_ID

    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=8)

    to_encode = {
        "sub": SYSTEM_USER_ID,
        "jti": secrets.token_urlsafe(16),
        "app_id": app_id,
        "org_id": org_id,
        "verified_params": verified_params,
        "email": "embed@internal.gobifrost.com",
        "is_superuser": False,
        "embed": True,
        "roles": ["EmbedUser"],
        "exp": expire,
        "type": "embed",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }

    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


# =============================================================================
# Secret Encryption (for storing secrets in database)
# =============================================================================


_FERNET_SALT = b"bifrost_secrets_v1"


def _get_fernet_key() -> bytes:
    """
    Derive a Fernet-compatible key from the application secret using HKDF.

    HKDF (HMAC-based Key Derivation Function) is more appropriate than PBKDF2
    when deriving keys from a high-entropy master key (as opposed to passwords).
    It's faster and provides better key separation with the info parameter.

    Returns:
        32-byte key suitable for Fernet encryption
    """
    settings = get_settings()

    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_FERNET_SALT,
        info=b"bifrost-secrets-encryption",
    )

    key = base64.urlsafe_b64encode(kdf.derive(settings.secret_key.encode()))
    return key


def derive_fernet_key(secret_key: str) -> bytes:
    """
    Derive a Fernet-compatible key from an explicit secret key.

    Same HKDF algorithm as _get_fernet_key() but accepts an explicit key
    instead of reading from settings. Used for import re-encryption.
    """
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_FERNET_SALT,
        info=b"bifrost-secrets-encryption",
    )
    return base64.urlsafe_b64encode(kdf.derive(secret_key.encode()))


def decrypt_with_key(encrypted: str, secret_key: str) -> str:
    """
    Decrypt a secret using an explicit key (not current instance settings).

    Used during import to decrypt values encrypted by a different instance.
    """
    key = derive_fernet_key(secret_key)
    f = Fernet(key)
    encrypted_bytes = base64.urlsafe_b64decode(encrypted.encode())
    return f.decrypt(encrypted_bytes).decode()


def encrypt_secret(plaintext: str) -> str:
    """
    Encrypt a secret value for storage in the database.

    Args:
        plaintext: The secret value to encrypt

    Returns:
        Base64-encoded encrypted value
    """
    key = _get_fernet_key()
    f = Fernet(key)
    encrypted = f.encrypt(plaintext.encode())
    return base64.urlsafe_b64encode(encrypted).decode()


def decrypt_secret(encrypted: str) -> str:
    """
    Decrypt a secret value from the database.

    Args:
        encrypted: Base64-encoded encrypted value

    Returns:
        Decrypted plaintext value
    """
    key = _get_fernet_key()
    f = Fernet(key)
    encrypted_bytes = base64.urlsafe_b64decode(encrypted.encode())
    decrypted = f.decrypt(encrypted_bytes)
    return decrypted.decode()


# =============================================================================
# CSRF Protection
# =============================================================================


def generate_csrf_token() -> str:
    """
    Generate a cryptographically secure CSRF token.

    Returns:
        URL-safe base64 encoded random string (43 characters)
    """
    return secrets.token_urlsafe(32)


def validate_csrf_token(cookie_token: str, header_token: str) -> bool:
    """
    Validate CSRF token using constant-time comparison.

    Args:
        cookie_token: CSRF token from cookie
        header_token: CSRF token from X-CSRF-Token header

    Returns:
        True if tokens match, False otherwise
    """
    if not cookie_token or not header_token:
        return False
    return secrets.compare_digest(cookie_token, header_token)


def authenticate_engine() -> None:
    """
    Create/refresh engine credentials for SDK calls in worker processes.

    This creates a long-lived superuser token and saves it to the credentials
    file (~/.bifrost/credentials.json). Called at the start of each workflow
    execution as a failsafe to ensure the token is always valid.

    The SDK's get_client() will find these credentials automatically, making
    the worker behave identically to CLI mode but with superuser privileges.
    """
    import os

    from bifrost.credentials import save_credentials

    # Use a fixed UUID for the engine service account
    # This is a well-known UUID that represents the execution engine
    ENGINE_USER_ID = "00000000-0000-0000-0000-000000000001"

    # Create a long-lived superuser token (30 days)
    # is_superuser=True with no org_id = system account with global access
    token_data = {
        "sub": ENGINE_USER_ID,
        "email": "engine@bifrost.internal",
        "name": "Bifrost Engine",
        "is_superuser": True,
    }

    token = create_access_token(
        token_data,
        expires_delta=timedelta(days=30)
    )

    # Get API URL for internal communication
    api_url = os.getenv("BIFROST_API_URL", "http://api:8000")

    # Calculate expiration timestamp
    expires_at = datetime.now(timezone.utc) + timedelta(days=30)

    # Save to credentials file - SDK will find this automatically
    save_credentials(
        api_url=api_url,
        access_token=token,
        refresh_token=token,  # Not used but required by schema
        expires_at=expires_at.isoformat(),
    )
