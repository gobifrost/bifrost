"""GitHub-only signing and authenticated ingress parsing; never Bifrost authority."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
import jwt


EVENT_PERMISSIONS = {
    "repository": "metadata",
    "pull_request": "pull_requests",
    "issues": "issues",
    "check_run": "checks",
    "check_suite": "checks",
    "release": "contents",
}
LIFECYCLE_EVENTS = frozenset({"installation", "installation_repositories", "installation_target"})
MAX_WEBHOOK_BYTES = 5 * 1024 * 1024


class GitHubAppError(Exception):
    """Only fixed, sanitized messages may cross an API/job boundary."""

    def __init__(self, code: str, message: str, status_code: int = 400, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after


def validate_private_key(pem: str) -> None:
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError):
        raise GitHubAppError("invalid_key", "Enter an unencrypted RSA private key") from None
    if not isinstance(key, RSAPrivateKey) or key.key_size < 2048:
        raise GitHubAppError("invalid_key", "Use an RSA private key of at least 2048 bits")


def issue_app_jwt(app_id: int, private_key: str, *, now: datetime | None = None) -> str:
    """Sign only the fixed GitHub App claim set with the external RSA key."""
    timestamp = int((now or datetime.now(timezone.utc)).timestamp())
    return jwt.encode({"iss": str(app_id), "iat": timestamp - 60, "exp": timestamp + 540}, private_key, algorithm="RS256")


def parse_verified_webhook(body: bytes, headers: dict[str, str], secret: str) -> tuple[str, str, dict[str, Any]]:
    if not secret:
        raise GitHubAppError("signature_configuration", "Webhook verification is not configured", 503)
    if len(body) > MAX_WEBHOOK_BYTES:
        raise GitHubAppError("payload_size", "Webhook payload is too large", 413)
    signature = headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature) or not hmac.compare_digest(signature, expected):
        raise GitHubAppError("signature", "Missing or invalid GitHub signature", 401)
    delivery_id = headers.get("x-github-delivery", "")
    event = headers.get("x-github-event", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery_id) or event not in {*EVENT_PERMISSIONS, *LIFECYCLE_EVENTS, "ping"}:
        raise GitHubAppError("event", "Missing delivery identity or unsupported GitHub event", 400)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise GitHubAppError("payload", "Invalid GitHub webhook JSON", 400) from None
    if not isinstance(payload, dict):
        raise GitHubAppError("payload", "GitHub webhook must contain an object", 400)
    return delivery_id, event, payload
