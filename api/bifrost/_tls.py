"""Process-local TLS configuration shared by SDK HTTP clients."""

from __future__ import annotations

import os
import ssl
import threading

import certifi


_context_lock = threading.Lock()
_cached_context: ssl.SSLContext | None = None
_cached_key: tuple[int, str, str, str | None] | None = None


def _reset_after_fork() -> None:
    """Discard inherited state that may belong to vanished parent threads."""
    global _context_lock, _cached_context, _cached_key

    _context_lock = threading.Lock()
    _cached_context = None
    _cached_key = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def _trust_store() -> tuple[tuple[int, str, str, str | None], dict[str, str]]:
    """Return HTTPX's effective trust-environment selection and cache key."""
    cert_file = os.environ.get("SSL_CERT_FILE")
    if cert_file:
        return (
            (os.getpid(), "file", cert_file, _relative_path_cwd(cert_file)),
            {"cafile": cert_file},
        )

    cert_directory = os.environ.get("SSL_CERT_DIR")
    if cert_directory:
        return (
            (os.getpid(), "directory", cert_directory, _relative_path_cwd(cert_directory)),
            {"capath": cert_directory},
        )

    cert_file = certifi.where()
    return (os.getpid(), "certifi", cert_file, None), {"cafile": cert_file}


def _relative_path_cwd(path: str) -> str | None:
    """Include the working directory when a trust-store path is relative."""
    return os.getcwd() if not os.path.isabs(path) else None


def shared_ssl_context() -> ssl.SSLContext:
    """Return a verified SSL context for the current HTTPX trust environment.

    HTTPX normally creates this context for every client. SDK clients can share
    it because its trust store is process-wide. The cache follows HTTPX's
    ``trust_env=True`` precedence and is replaced when the process, selected
    environment value, or relative-path working directory changes.
    """
    global _cached_context, _cached_key

    cache_key, context_kwargs = _trust_store()
    with _context_lock:
        if _cached_context is None or _cached_key != cache_key:
            _cached_context = ssl.create_default_context(**context_kwargs)
            _cached_key = cache_key
        return _cached_context
