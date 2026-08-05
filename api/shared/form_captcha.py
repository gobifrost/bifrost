"""Self-hosted proof-of-work protection for anonymous public forms."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import altcha
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORM_CAPTCHA_TTL_SECONDS = 5 * 60
FORM_CAPTCHA_ALGORITHM = "PBKDF2/SHA-256"
FORM_CAPTCHA_COST = 2_500
_FORM_CAPTCHA_HKDF_SALT = b"bifrost-form-captcha-v1"
_FORM_CAPTCHA_HKDF_INFO = b"anonymous-public-form-proof-of-work"


class FormCaptchaError(ValueError):
    """Raised when a public-form proof is missing, invalid, or expired."""


@dataclass(frozen=True)
class VerifiedFormCaptcha:
    """Identity and lifetime recovered from one verified challenge payload."""

    challenge_id: str
    expires_at: int


def derive_form_captcha_secret(master_secret: str) -> bytes:
    """Derive a domain-separated ALTCHA signing key from BIFROST_SECRET_KEY."""

    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_FORM_CAPTCHA_HKDF_SALT,
        info=_FORM_CAPTCHA_HKDF_INFO,
    ).derive(master_secret.encode("utf-8"))


def create_form_captcha_challenge(
    *,
    master_secret: str,
    form_id: str,
    session_id: str,
    session_expires_at: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a short-lived challenge signed for one form session."""

    current = now or datetime.now(timezone.utc)
    expires_at = int((current + timedelta(seconds=FORM_CAPTCHA_TTL_SECONDS)).timestamp())
    if session_expires_at is not None:
        expires_at = min(expires_at, session_expires_at)
    if expires_at <= int(current.timestamp()):
        raise FormCaptchaError("Form session has expired")

    challenge = altcha.create_challenge(
        algorithm=FORM_CAPTCHA_ALGORITHM,
        cost=FORM_CAPTCHA_COST,
        expires_at=expires_at,
        data={
            "challenge_id": secrets.token_urlsafe(24),
            "form_id": form_id,
            "session_id": session_id,
        },
        hmac_secret=derive_form_captcha_secret(master_secret),
    )
    return challenge.to_dict()


def verify_form_captcha_solution(
    *,
    payload: str | None,
    master_secret: str,
    form_id: str,
    session_id: str,
) -> VerifiedFormCaptcha:
    """Verify proof integrity, work, expiry, and exact form-session binding."""

    if not payload:
        raise FormCaptchaError("Verification is required")

    try:
        decoded = altcha.Payload.from_base64(payload)
    except (TypeError, ValueError, KeyError):
        raise FormCaptchaError("Verification is invalid or expired") from None

    result = altcha.verify_solution(
        decoded,
        derive_form_captcha_secret(master_secret),
    )
    if not result.verified:
        raise FormCaptchaError("Verification is invalid or expired")

    parameters = decoded.challenge.parameters
    data = parameters.data
    if not isinstance(data, dict):
        raise FormCaptchaError("Verification is invalid or expired")

    challenge_id = data.get("challenge_id")
    if (
        not isinstance(challenge_id, str)
        or not secrets.compare_digest(str(data.get("form_id", "")), form_id)
        or not secrets.compare_digest(str(data.get("session_id", "")), session_id)
    ):
        raise FormCaptchaError("Verification is invalid or expired")

    expires_at = parameters.expires_at
    if not isinstance(expires_at, int):
        raise FormCaptchaError("Verification is invalid or expired")
    return VerifiedFormCaptcha(challenge_id=challenge_id, expires_at=expires_at)


async def redeem_form_captcha_solution(
    *,
    payload: str | None,
    master_secret: str,
    form_id: str,
    session_id: str,
) -> None:
    """Verify and atomically redeem a challenge exactly once."""

    from src.core.cache.redis_client import get_redis

    verified = verify_form_captcha_solution(
        payload=payload,
        master_secret=master_secret,
        form_id=form_id,
        session_id=session_id,
    )
    now = int(datetime.now(timezone.utc).timestamp())
    ttl = verified.expires_at - now
    if ttl <= 0:
        raise FormCaptchaError("Verification is invalid or expired")

    key = f"bifrost:form:captcha:{session_id}:{verified.challenge_id}"
    async with get_redis() as redis:
        redeemed = await redis.set(key, "1", ex=ttl, nx=True)
    if not redeemed:
        raise FormCaptchaError("Verification has already been used")
