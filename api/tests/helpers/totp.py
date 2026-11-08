"""
TOTP Helper for Testing

Provides utilities for generating TOTP codes during E2E testing.
"""

import time

import httpx
import pyotp

LOGIN_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

# Most recently accepted code per secret, so a test can prove that submitting
# the same code again is refused.
_last_accepted_code: dict[str, str] = {}


def last_accepted_totp_code(secret: str) -> str | None:
    """Return the last code accepted by :func:`complete_mfa_login`."""
    return _last_accepted_code.get(secret)


def generate_totp_code(secret: str) -> str:
    """
    Generate a valid 6-digit TOTP code from a base32 secret.

    Args:
        secret: Base32-encoded TOTP secret from MFA setup

    Returns:
        6-digit TOTP code as string
    """
    return pyotp.TOTP(secret).now()


def create_totp(secret: str) -> pyotp.TOTP:
    """
    Create a TOTP instance for repeated code generation.

    Args:
        secret: Base32-encoded TOTP secret from MFA setup

    Returns:
        pyotp.TOTP instance
    """
    return pyotp.TOTP(secret)


def verify_totp_code(secret: str, code: str) -> bool:
    """
    Verify a TOTP code against a secret.

    Args:
        secret: Base32-encoded TOTP secret
        code: 6-digit TOTP code to verify

    Returns:
        True if valid, False otherwise
    """
    return pyotp.TOTP(secret).verify(code)


def record_accepted_totp_code(secret: str, code: str) -> None:
    """Track successful enrollment as well as login for shared E2E users."""
    _last_accepted_code[secret] = code


def complete_mfa_login(
    client: httpx.Client,
    mfa_token: str,
    secret: str,
) -> httpx.Response:
    """Submit once, waiting beforehand only for a known consumed code."""
    code = generate_totp_code(secret)
    if code == last_accepted_totp_code(secret):
        interval = pyotp.TOTP(secret).interval
        time.sleep(interval - (time.time() % interval) + 0.5)
        code = generate_totp_code(secret)
    response = client.post(
        "/auth/mfa/login",
        json={"mfa_token": mfa_token, "code": code},
    )
    if response.status_code == 200:
        record_accepted_totp_code(secret, code)
    return response


def login_with_totp(
    client: httpx.Client,
    email: str,
    password: str,
    secret: str,
) -> httpx.Response:
    """Password login plus one MFA submission using a fresh code."""
    response = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers=LOGIN_FORM_HEADERS,
    )
    if response.status_code != 200:
        return response
    data = response.json()
    if not data.get("mfa_required"):
        return response
    return complete_mfa_login(client, data["mfa_token"], secret)
