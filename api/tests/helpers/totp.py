"""
TOTP Helper for Testing

Provides utilities for generating TOTP codes during E2E testing.
"""

import time

import httpx
import pyotp

LOGIN_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

# TOTP codes are single-use per time step once a login succeeds. A test-run can
# issue several logins for the same user inside one 30-second step, so helpers
# here wait for the next step when a code has already been accepted instead of
# weakening replay protection in the application.
_MFA_REPLAY_DETAIL = "Invalid MFA code"

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


def seconds_until_next_totp_step(secret: str) -> float:
    """Seconds to wait until the next TOTP time step starts."""
    totp = pyotp.TOTP(secret)
    return totp.interval - (time.time() % totp.interval) + 0.5


def complete_mfa_login(
    client: httpx.Client,
    mfa_token: str,
    secret: str,
    *,
    attempts: int = 3,
) -> httpx.Response:
    """Complete an MFA challenge, using a fresh time step when one is replayed.

    The application rejects a code whose time step was already accepted. Tests
    that log in repeatedly within one step wait for the next step, which is what
    a person would do after being told the code was already used.
    """
    response: httpx.Response | None = None
    for attempt in range(attempts):
        code = generate_totp_code(secret)
        response = client.post(
            "/auth/mfa/login",
            json={"mfa_token": mfa_token, "code": code},
        )
        if response.status_code == 200:
            _last_accepted_code[secret] = code
            return response

        detail = ""
        try:
            detail = response.json().get("detail", "")
        except ValueError:
            detail = response.text
        if detail != _MFA_REPLAY_DETAIL or attempt + 1 == attempts:
            return response
        time.sleep(seconds_until_next_totp_step(secret))

    assert response is not None
    return response


def login_with_totp(
    client: httpx.Client,
    email: str,
    password: str,
    secret: str,
    *,
    attempts: int = 3,
) -> httpx.Response:
    """Password login plus MFA, tolerating an already-used TOTP time step."""
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
    return complete_mfa_login(client, data["mfa_token"], secret, attempts=attempts)
