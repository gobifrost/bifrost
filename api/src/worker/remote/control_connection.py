"""Optional service authentication for the physical launcher's control socket."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit

HEADERS_ENV = "BIFROST_WORKER_CONTROL_HEADERS"
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_RESERVED = {"host", "connection", "upgrade", "content-length", "transfer-encoding",
             "proxy-authorization", "proxy-connection", "keep-alive", "te", "trailer"}


def load_control_headers(control_url: str) -> dict[str, str]:
    """Validate configuration before connecting; errors never echo credentials."""
    raw = os.environ.get(HEADERS_ENV)
    if raw is None:
        return {}
    if len(raw.encode("utf-8")) > 8192:
        raise ValueError("Worker-control headers exceed the configuration limit")
    try:
        pairs = json.loads(raw, object_pairs_hook=list)
    except (ValueError, TypeError):
        raise ValueError("Worker-control headers must be a JSON object of strings") from None
    # object_pairs_hook preserves duplicates. An array must not impersonate an object.
    if not raw.lstrip().startswith("{") or not isinstance(pairs, list) or len(pairs) > 16:
        raise ValueError("Worker-control headers must be a JSON object of at most 16 strings")
    headers: dict[str, str] = {}
    names: set[str] = set()
    for name, value in pairs:
        if (not isinstance(name, str) or _HEADER_NAME.fullmatch(name) is None
                or not isinstance(value, str) or not value.strip()
                or any(ord(char) < 32 or ord(char) > 126 for char in value)):
            raise ValueError("Invalid worker-control header name or value")
        normalized = name.lower()
        if normalized in names or normalized in _RESERVED or normalized.startswith("sec-websocket-"):
            raise ValueError("Duplicate or reserved worker-control header")
        names.add(normalized)
        headers[name] = value
    if headers:
        url = urlsplit(control_url)
        if (url.scheme != "wss" or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment):
            raise ValueError("Service-authenticated worker control requires a WSS URL without credentials, query or fragment")
    return headers


def connect_control(control_url: str, headers: dict[str, str]) -> Any:
    """Preserve ordinary connections; authenticated requests never follow redirects."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus, SecurityError

    if not headers:
        return connect(control_url, max_size=2**20, open_timeout=10)

    class ServiceAuthConnect(connect):
        def process_redirect(self, exc: Exception) -> Exception | str:
            if isinstance(exc, InvalidStatus) and 300 <= exc.response.status_code < 400:
                return SecurityError("Service-authenticated worker-control redirects are disabled")
            return super().process_redirect(exc)

    # websockets DEBUG output includes plaintext handshake headers. Keep this
    # connection's wire logger private; the launcher still logs lifecycle/error types.
    wire_logger = logging.Logger("bifrost.worker.control.authenticated")
    wire_logger.disabled = True
    return ServiceAuthConnect(
        control_url, additional_headers=headers, max_size=2**20,
        open_timeout=10, logger=wire_logger,
    )
