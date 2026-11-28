"""Shared public HTTPS destination validation for server-side metadata fetches."""

import ipaddress
import socket
from urllib.parse import urlparse


def validate_public_https_url(
    url: str, *, allowed_hosts: set[str] | None = None, allowed_hosts_name: str = "the allowed hosts"
) -> None:
    """Validate a URL is safe to fetch (https only, public address).

    Prevents SSRF by rejecting non-https schemes and hostnames that resolve
    to private, loopback, link-local, reserved, or multicast addresses.
    A caller may additionally restrict the public hostnames it permits.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only https URLs are allowed, got {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("URL must have a hostname")
    # Validate port syntax/range before a transport-specific exception.
    _ = parsed.port

    hostname = parsed.hostname.lower()
    if allowed_hosts is not None and hostname not in allowed_hosts:
        raise ValueError(
            f"Hostname {hostname!r} is not in {allowed_hosts_name}"
        )

    try:
        addr_info = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve hostname {parsed.hostname}: {e}") from e
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
                f"URL hostname resolves to non-public address: {ip_str}"
            )
