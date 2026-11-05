"""OAuth request binding and callback validation shared by browser consent and MCP."""

import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from ipaddress import ip_address

_AUTH_CODE_TAG = re.compile(r"[0-9a-f]{64}")
TTL_MCP_AUTH_CODE = 300

def _secret_hmac(message: str) -> str:
    """Hex HMAC-SHA256 of ``message`` under the platform secret key."""
    from src.config import get_settings

    return hmac.new(
        get_settings().secret_key.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def _mcp_auth_code_key(code: str) -> str:
    """Key for MCP authorization code storage, derived from the code with the server secret."""
    return f"bifrost:mcp:auth_code:{_secret_hmac(code)}"


def _auth_code_tag(code: str, record: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"code": code, "record": record}, sort_keys=True, separators=(",", ":")
    )
    return _secret_hmac(canonical)


def _seal_auth_code_record(code: str, record: dict[str, Any]) -> str:
    """Serialize an authorization code record with its server-secret tag."""
    return json.dumps({"record": record, "tag": _auth_code_tag(code, record)})


def _open_auth_code_record(code: str, stored_json: str) -> dict[str, Any] | None:
    """Return the stored record for ``code`` if its tag verifies, else None."""
    try:
        stored = json.loads(stored_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(stored, dict):
        return None
    record = stored.get("record")
    tag = stored.get("tag")
    if not isinstance(record, dict) or not isinstance(tag, str):
        return None
    if not _AUTH_CODE_TAG.fullmatch(tag):
        return None
    if not hmac.compare_digest(_auth_code_tag(code, record), tag):
        return None
    return record


def _mcp_state_key(state: str) -> str:
    """Key for OAuth state during authorization flow."""
    return f"bifrost:mcp:state:{state}"


def validate_redirect_uri(uri: str) -> None:
    """Accept secure web callbacks and native app callbacks, without fragments."""
    if not uri or any(char.isspace() or ord(char) < 32 for char in uri):
        raise ValueError("Invalid callback address")
    parsed = urlsplit(uri)
    # Accessing port also rejects invalid or out-of-range ports.
    _ = parsed.port
    if not parsed.scheme or "#" in uri or parsed.username or parsed.password:
        raise ValueError("Invalid callback address")
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and _is_loopback(parsed.hostname):
        return
    if parsed.scheme not in {"http", "https", "javascript", "data", "file", "vbscript", "about", "blob", "ftp"}:
        if parsed.netloc or parsed.path.startswith("/"):
            return
    raise ValueError("Use HTTPS, a local callback, or a native app callback")


def _is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return host is not None and ip_address(host).is_loopback
    except ValueError:
        return False


def redirect_matches(requested: str, registered: str) -> bool:
    """Exact matching, except HTTP loopback ports may vary (RFC 8252)."""
    try:
        validate_redirect_uri(requested)
        validate_redirect_uri(registered)
        if requested == registered:
            return True
        left, right = urlsplit(requested), urlsplit(registered)
        if left.scheme != "http" or right.scheme != "http" or not _is_loopback(left.hostname):
            return False
        # Keep host spelling, path and query exact; remove only an explicit port.
        def without_port(uri: str) -> str:
            parsed = urlsplit(uri)
            authority = parsed.netloc
            if parsed.port is not None:
                authority = authority.rsplit(":", 1)[0]
            return urlunsplit(parsed._replace(netloc=authority))
        return without_port(requested) == without_port(registered)
    except ValueError:
        return False


def callback_response(uri: str, **parameters: str) -> str:
    """Preserve registered query parameters and encode response values safely."""
    parsed = urlsplit(uri)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in parameters]
    query.extend(parameters.items())
    return urlunsplit(parsed._replace(query=urlencode(query)))


