"""
SSRF mitigation for admin-configured embedding endpoints.

The whole `/api/admin/llm/*` surface is gated by `RequirePlatformAdmin`, but
admin-supplied URLs still flow into outbound HTTP requests — partial SSRF.
This module validates a URL is safe to fetch:

- Scheme must be `http` or `https` (`http` is allowed because Ollama and
  some local-LLM setups don't terminate TLS).
- Hostname must resolve to a public address (rejects private RFC1918,
  loopback, link-local, reserved, multicast, unspecified).

To support Ollama / local LLMs that legitimately bind to private addresses,
operators can opt-in trusted hostnames via the `EMBEDDING_ALLOWED_HOSTS`
env var (comma-separated, case-insensitive, exact match). Hosts in that list
skip the address-class check entirely.

The "right" trust boundary is admin auth, but defense-in-depth here is cheap
and matches the existing `_validate_spec_url` helper in services/sdk_generator.py.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


_ALLOWED_HOSTS_ENV = "EMBEDDING_ALLOWED_HOSTS"


def _allowed_hosts() -> set[str]:
    raw = os.environ.get(_ALLOWED_HOSTS_ENV, "").strip()
    if not raw:
        return set()
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def validate_embedding_endpoint(url: str) -> str:
    """
    Validate `url` is safe for the embedding client to fetch.

    Returns the URL rebuilt from parsed components on success — callers should
    use this returned value rather than the input, both because (a) it goes
    through urlparse/urlunparse so any odd serialization quirks normalize, and
    (b) CodeQL's data-flow analysis recognizes "value returned by sanitizer
    function" as cleansed input, but does NOT recognize "raises on bad input,
    so subsequent use of the original variable is safe."

    Pass criteria:
    - http or https scheme.
    - Has a hostname.
    - Either the hostname is in EMBEDDING_ALLOWED_HOSTS, OR every IP it
      resolves to is a public unicast address.

    Raises ValueError on any rejection. DNS is resolved at validation time;
    there's a TOCTOU window between validate-and-use that matches the
    existing pattern in `services/sdk_generator.py`.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Embedding endpoint must be http or https, got {parsed.scheme!r}"
        )
    if not parsed.hostname:
        raise ValueError("Embedding endpoint URL must have a hostname")

    hostname = parsed.hostname.lower()

    if hostname not in _allowed_hosts():
        try:
            addr_info = socket.getaddrinfo(hostname, None)
        except socket.gaierror as e:
            raise ValueError(
                f"Cannot resolve embedding endpoint hostname {hostname!r}: {e}"
            ) from e

        for _family, _type, _proto, _canon, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise ValueError(
                    f"Embedding endpoint {hostname!r} resolves to non-public "
                    f"address {ip_str}. Add {hostname!r} to "
                    f"{_ALLOWED_HOSTS_ENV} to allow it explicitly."
                )

    # Return a URL rebuilt from the parsed components. CodeQL recognizes the
    # return value of a sanitizer as cleansed; using the original `url`
    # variable downstream would still flow user input.
    safe_scheme = parsed.scheme
    safe_netloc = parsed.netloc
    safe_path = parsed.path
    return f"{safe_scheme}://{safe_netloc}{safe_path}"


__all__ = ["validate_embedding_endpoint"]
