"""Optional Sentry integration with conservative privacy defaults."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "database_url",
    "dsn",
    "id_token",
    "jwt",
    "password",
    "refresh_token",
    "secret",
    "secret_key",
    "session",
    "token",
    "x-api-key",
}

FILTERED = "[Filtered]"


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(sensitive.replace("-", "_") in normalized for sensitive in SENSITIVE_KEYS)


def _scrub_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.query:
        return value

    query = []
    changed = False
    for key, query_value in parse_qsl(parts.query, keep_blank_values=True):
        if _is_sensitive_key(key):
            query.append((key, FILTERED))
            changed = True
        else:
            query.append((key, query_value))

    if not changed:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        scrubbed: dict[Any, Any] = {}
        for key, item in value.items():
            scrubbed[key] = FILTERED if _is_sensitive_key(key) else _scrub(item)
        return scrubbed
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item) for item in value)
    return value


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Scrub sensitive data from Sentry events before upload."""
    scrubbed = _scrub(deepcopy(event))
    request = scrubbed.get("request")
    if isinstance(request, dict):
        request["cookies"] = FILTERED if request.get("cookies") else request.get("cookies")
        url = request.get("url")
        if isinstance(url, str):
            request["url"] = _scrub_url(url)
    return scrubbed


def configure_sentry(settings: Any | None = None, sentry_sdk_module: Any | None = None) -> bool:
    """Initialize Sentry if BIFROST_SENTRY_DSN is configured.

    Returns True when Sentry was initialized and False when it is intentionally
    disabled or the optional SDK dependency is unavailable.
    """
    if settings is None:
        from src.config import get_settings

        settings = get_settings()

    dsn = getattr(settings, "sentry_dsn", None)
    if not dsn:
        return False

    if sentry_sdk_module is None:
        try:
            import sentry_sdk as sentry_sdk_module
        except ImportError:
            return False

    sentry_sdk_module.init(
        dsn=dsn,
        environment=getattr(settings, "environment", None),
        send_default_pii=getattr(settings, "sentry_send_default_pii", False),
        enable_logs=False,
        traces_sample_rate=getattr(settings, "sentry_traces_sample_rate", 0.0),
        profile_session_sample_rate=getattr(settings, "sentry_profiles_sample_rate", 0.0),
        before_send=before_send,
    )
    return True
