"""Tests for sdk_generator URL validation (SSRF defense).

Covers _validate_spec_url:
- https-only enforcement
- public-IP enforcement (rejects private/loopback/etc.)
- optional SDK_GENERATOR_ALLOWED_HOSTS allowlist
"""

from __future__ import annotations

import pytest

from src.services.sdk_generator import _validate_spec_url


def _fake_getaddrinfo_public(_host: str, _port: int | None) -> list[tuple]:
    """Pretend the hostname resolves to a public IP (1.1.1.1)."""
    return [(2, 1, 6, "", ("1.1.1.1", 0))]


def _fake_getaddrinfo_private(_host: str, _port: int | None) -> list[tuple]:
    """Pretend the hostname resolves to an RFC1918 IP."""
    return [(2, 1, 6, "", ("10.0.0.1", 0))]


def test_rejects_non_https_scheme() -> None:
    with pytest.raises(ValueError, match="Only https URLs are allowed"):
        _validate_spec_url("http://example.com/openapi.json")


def test_rejects_private_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shared.url_safety.socket.getaddrinfo",
        _fake_getaddrinfo_private,
    )
    monkeypatch.delenv("SDK_GENERATOR_ALLOWED_HOSTS", raising=False)
    with pytest.raises(ValueError, match="non-public address"):
        _validate_spec_url("https://example.com/openapi.json")


def test_allows_public_address_when_no_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default behavior: any https URL resolving to a public IP is allowed."""
    monkeypatch.setattr(
        "shared.url_safety.socket.getaddrinfo",
        _fake_getaddrinfo_public,
    )
    monkeypatch.delenv("SDK_GENERATOR_ALLOWED_HOSTS", raising=False)
    # No exception
    _validate_spec_url("https://example.com/openapi.json")


def test_allowlist_permits_listed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shared.url_safety.socket.getaddrinfo",
        _fake_getaddrinfo_public,
    )
    monkeypatch.setenv(
        "SDK_GENERATOR_ALLOWED_HOSTS",
        "api.example.com, docs.example.com",
    )
    _validate_spec_url("https://api.example.com/openapi.json")
    _validate_spec_url("https://docs.example.com/spec.yaml")


def test_allowlist_blocks_unlisted_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shared.url_safety.socket.getaddrinfo",
        _fake_getaddrinfo_public,
    )
    monkeypatch.setenv("SDK_GENERATOR_ALLOWED_HOSTS", "api.example.com")
    with pytest.raises(ValueError, match="not in SDK_GENERATOR_ALLOWED_HOSTS"):
        _validate_spec_url("https://other.example.com/openapi.json")


def test_allowlist_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shared.url_safety.socket.getaddrinfo",
        _fake_getaddrinfo_public,
    )
    monkeypatch.setenv("SDK_GENERATOR_ALLOWED_HOSTS", "API.Example.com")
    _validate_spec_url("https://api.example.com/openapi.json")


def test_empty_allowlist_env_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "shared.url_safety.socket.getaddrinfo",
        _fake_getaddrinfo_public,
    )
    monkeypatch.setenv("SDK_GENERATOR_ALLOWED_HOSTS", "   ")
    _validate_spec_url("https://anything.example.com/openapi.json")


def test_unresolvable_hostname_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket as _socket

    def _raise(*_a, **_k):
        raise _socket.gaierror("no resolution")

    monkeypatch.setattr(
        "shared.url_safety.socket.getaddrinfo",
        _raise,
    )
    monkeypatch.delenv("SDK_GENERATOR_ALLOWED_HOSTS", raising=False)
    with pytest.raises(ValueError, match="Cannot resolve hostname"):
        _validate_spec_url("https://nope.invalid/openapi.json")
