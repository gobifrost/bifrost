"""
Unit tests for the embedding endpoint URL validator.

Covers:
- Scheme/hostname rejection
- Public address pass-through
- Private/loopback/link-local rejection
- EMBEDDING_ALLOWED_HOSTS opt-in for trusted internal hosts (Ollama use case)
"""

from unittest.mock import patch

import pytest

from src.services.embeddings import url_safety


def _addr_info(ip: str):
    """Shape that socket.getaddrinfo returns: (family, type, proto, canon, sockaddr)."""
    return [(0, 0, 0, "", (ip, 0))]


def test_https_public_address_passes():
    with patch.object(url_safety.socket, "getaddrinfo", return_value=_addr_info("8.8.8.8")):
        result = url_safety.validate_embedding_endpoint("https://api.openai.com/v1")
    # Validator returns a sanitized URL — CodeQL recognizes return-value-of-sanitizer.
    assert result == "https://api.openai.com/v1"


def test_http_public_address_passes():
    """HTTP is permitted because some hosted LLMs don't terminate TLS."""
    with patch.object(url_safety.socket, "getaddrinfo", return_value=_addr_info("8.8.8.8")):
        result = url_safety.validate_embedding_endpoint("http://api.example.com/v1")
    assert result == "http://api.example.com/v1"


def test_non_http_scheme_rejected():
    with pytest.raises(ValueError, match="must be http or https"):
        url_safety.validate_embedding_endpoint("ftp://example.com/v1")


def test_missing_hostname_rejected():
    with pytest.raises(ValueError, match="must have a hostname"):
        url_safety.validate_embedding_endpoint("https://")


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # RFC1918
        "192.168.1.1",  # RFC1918
        "172.16.0.1",  # RFC1918
        "169.254.1.1",  # link-local
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
    ],
)
def test_private_or_loopback_address_rejected(ip):
    with patch.object(url_safety.socket, "getaddrinfo", return_value=_addr_info(ip)):
        with pytest.raises(ValueError, match="non-public"):
            url_safety.validate_embedding_endpoint("https://internal.local")


def test_unresolvable_hostname_rejected():
    import socket as _socket

    with patch.object(
        url_safety.socket,
        "getaddrinfo",
        side_effect=_socket.gaierror("Name or service not known"),
    ):
        with pytest.raises(ValueError, match="Cannot resolve"):
            url_safety.validate_embedding_endpoint("https://nope.invalid")


def test_allowlisted_host_skips_address_check(monkeypatch):
    """Hosts in EMBEDDING_ALLOWED_HOSTS bypass the public-address requirement.

    This is the Ollama escape hatch: operators who want to point at
    http://ollama.local can opt-in their hostname without code changes.
    """
    monkeypatch.setenv("EMBEDDING_ALLOWED_HOSTS", "ollama.local,internal.example.com")
    # No getaddrinfo patch — if it gets called, the test fails loudly because
    # the real DNS lookup will probably succeed but might resolve to private,
    # and we want to assert we never even tried.
    with patch.object(url_safety.socket, "getaddrinfo") as mock_getaddrinfo:
        url_safety.validate_embedding_endpoint("http://ollama.local:11434/v1")
        mock_getaddrinfo.assert_not_called()


def test_allowlist_match_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("EMBEDDING_ALLOWED_HOSTS", "Ollama.Local")
    with patch.object(url_safety.socket, "getaddrinfo") as mock_getaddrinfo:
        url_safety.validate_embedding_endpoint("http://OLLAMA.local:11434")
        mock_getaddrinfo.assert_not_called()


def test_allowlist_does_not_match_unrelated_host(monkeypatch):
    monkeypatch.setenv("EMBEDDING_ALLOWED_HOSTS", "ollama.local")
    with patch.object(url_safety.socket, "getaddrinfo", return_value=_addr_info("127.0.0.1")):
        with pytest.raises(ValueError, match="non-public"):
            url_safety.validate_embedding_endpoint("http://other.local")


def test_return_value_uses_urlunparse_not_fstring():
    """Regression guard for #213.

    The validator must build its return value via urlunparse(), not an
    f-string of parsed components. CodeQL's built-in py/partial-ssrf
    sanitizer model recognizes urlunparse's return as cleansed input;
    an f-string of the same components is taint-preserving from a
    data-flow perspective and re-opens the SSRF alert at every site
    that calls into this validator.

    A future refactor that swaps urlunparse back to f-string concatenation
    would close this test AND silently re-introduce the SSRF alert.
    """
    import ast
    import inspect

    src = inspect.getsource(url_safety.validate_embedding_endpoint)
    tree = ast.parse(src.lstrip())
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)

    # The function must end with a `return urlunparse(...)` — not a return
    # of an f-string or string concatenation of parsed components. CodeQL's
    # SSRF sanitizer model only recognizes the former as cleansed.
    return_stmts = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
    assert return_stmts, "validator must have a return statement"
    final_return = return_stmts[-1]
    assert isinstance(final_return.value, ast.Call), (
        "final return must be a call to urlunparse, got "
        f"{ast.dump(final_return.value) if final_return.value else 'None'}"
    )
    callee = final_return.value.func
    assert isinstance(callee, ast.Name) and callee.id == "urlunparse", (
        "final return must call urlunparse() — see #213 for why "
        f"f-string component concat re-opens py/partial-ssrf. Got: {callee}"
    )
