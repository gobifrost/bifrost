"""Unit tests for the SDK's process-local TLS context."""

from __future__ import annotations

import importlib
import os
import select
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import certifi
import pytest


def _tls_module():
    """Reload the helper so each test starts with an empty process cache."""
    import bifrost._tls

    return importlib.reload(bifrost._tls)


def test_shared_context_verifies_certificates_and_hostnames(monkeypatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    tls = _tls_module()

    context = tls.shared_ssl_context()

    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_shared_context_constructs_once_for_concurrent_first_call(monkeypatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    tls = _tls_module()
    context = ssl.create_default_context()
    create_context = Mock(return_value=context)
    monkeypatch.setattr(tls.ssl, "create_default_context", create_context)

    barrier = threading.Barrier(12)

    def get_context() -> ssl.SSLContext:
        barrier.wait()
        return tls.shared_ssl_context()

    with ThreadPoolExecutor(max_workers=12) as executor:
        contexts = list(executor.map(lambda _: get_context(), range(12)))

    assert contexts == [context] * 12
    create_context.assert_called_once_with(cafile=certifi.where())


def test_changed_cert_file_selection_reloads_and_invalid_path_raises(monkeypatch) -> None:
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    tls = _tls_module()

    first_context = tls.shared_ssl_context()
    missing_ca_file = "/definitely-missing-bifrost-ca.pem"
    monkeypatch.setenv("SSL_CERT_FILE", missing_ca_file)

    with pytest.raises(FileNotFoundError):
        tls.shared_ssl_context()

    assert first_context.verify_mode is ssl.CERT_REQUIRED


def test_cert_file_selection_takes_precedence_over_cert_directory(monkeypatch) -> None:
    tls = _tls_module()
    create_context = Mock(return_value=ssl.create_default_context())
    monkeypatch.setattr(tls.ssl, "create_default_context", create_context)
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/file.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/custom/certs")

    tls.shared_ssl_context()

    create_context.assert_called_once_with(cafile="/custom/file.pem")


def test_cert_directory_selection_is_used_when_no_cert_file(monkeypatch) -> None:
    tls = _tls_module()
    create_context = Mock(return_value=ssl.create_default_context())
    monkeypatch.setattr(tls.ssl, "create_default_context", create_context)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("SSL_CERT_DIR", "/custom/certs")

    tls.shared_ssl_context()

    create_context.assert_called_once_with(capath="/custom/certs")


def test_relative_ca_path_cache_key_tracks_current_directory(monkeypatch, tmp_path) -> None:
    tls = _tls_module()
    real_create_context = ssl.create_default_context
    create_context = Mock(side_effect=lambda **_kwargs: real_create_context())
    monkeypatch.setattr(tls.ssl, "create_default_context", create_context)
    monkeypatch.setenv("SSL_CERT_FILE", "ca.pem")
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    monkeypatch.chdir(first_directory)
    first_context = tls.shared_ssl_context()
    monkeypatch.chdir(second_directory)
    second_context = tls.shared_ssl_context()

    assert first_context is not second_context
    assert create_context.call_count == 2


def test_cache_is_independent_after_process_id_changes(monkeypatch) -> None:
    tls = _tls_module()
    real_create_context = ssl.create_default_context
    create_context = Mock(side_effect=lambda **_kwargs: real_create_context())
    monkeypatch.setattr(tls.ssl, "create_default_context", create_context)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(tls.os, "getpid", Mock(side_effect=[101, 101, 202]))

    parent_context = tls.shared_ssl_context()
    assert tls.shared_ssl_context() is parent_context
    child_context = tls.shared_ssl_context()

    assert child_context is not parent_context
    assert create_context.call_count == 2


def test_child_after_fork_replaces_an_inherited_locked_cache(monkeypatch) -> None:
    tls = _tls_module()
    monkeypatch.setattr(tls.ssl, "create_default_context", Mock(return_value=object()))
    tls.shared_ssl_context()

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_context_lock() -> None:
        with tls._context_lock:
            lock_acquired.set()
            release_lock.wait()

    lock_holder = threading.Thread(target=hold_context_lock, daemon=True)
    lock_holder.start()
    assert lock_acquired.wait(timeout=2)

    read_fd, write_fd = os.pipe()
    child_pid: int | None = None
    try:
        child_pid = os.fork()
        if child_pid == 0:
            os.close(read_fd)
            try:
                tls.shared_ssl_context()
                os.write(write_fd, b"ok")
            except BaseException:
                os.write(write_fd, b"error")
                os._exit(1)
            finally:
                os.close(write_fd)
            os._exit(0)

        os.close(write_fd)
        write_fd = -1
        readable, _, _ = select.select([read_fd], [], [], 2)
        assert readable
        result = os.read(read_fd, 5)
        _, child_status = os.waitpid(child_pid, 0)
        child_pid = None

        assert result == b"ok"
        assert os.WIFEXITED(child_status)
        assert os.WEXITSTATUS(child_status) == 0
    finally:
        if child_pid is not None:
            waited_pid, _ = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == 0:
                os.kill(child_pid, 9)
                os.waitpid(child_pid, 0)
        if write_fd != -1:
            os.close(write_fd)
        os.close(read_fd)
        release_lock.set()
        lock_holder.join(timeout=2)
        assert not lock_holder.is_alive()
