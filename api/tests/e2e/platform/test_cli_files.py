"""End-to-end test: the ``bifrost files`` CLI works from inside a worker.

This is the load-bearing test for the Buildfrost use case — proves a
workflow can shell out to ``bifrost files <verb>`` and have it
authenticate via engine credentials and round-trip a file.

Tests run inside the ``test-runner`` container, which is built from the
same ``api/Dockerfile.dev`` as the worker, so the ``/usr/local/bin/bifrost``
shim is on PATH here just like in the worker. We seed engine credentials
by calling :func:`src.core.security.authenticate_engine` directly (the
worker invokes the same function at the start of every execution — see
``api/src/services/execution/worker.py``), then exercise the shipped
``bifrost`` binary via :mod:`subprocess`. This proves:

1. The shim is on PATH in the image.
2. ``bifrost`` picks up ``~/.bifrost/credentials.json`` automatically.
3. The CLI hits the real ``http://api:8000`` and round-trips workspace files.

The test_cli_*.py siblings (agents, tables, configs, ...) exercise the
CLI in-process via :class:`click.testing.CliRunner` to test command
plumbing. This file is intentionally different: it exercises the
*compiled artifact path* (PATH lookup -> shim -> ``python -m bifrost``)
that real worker subprocesses take, which is exactly what the Buildfrost
agent relies on.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid

import pytest


@pytest.fixture
def engine_creds():
    """Populate ``~/.bifrost/credentials.json`` with an engine token.

    Mirrors what ``_run_execution`` does at the top of every workflow
    invocation in the worker (`api/src/services/execution/worker.py:134`).
    Without this, the CLI has nothing to authenticate with.

    Cleans up on teardown so other e2e tests that rely on "no credentials
    present" semantics (e.g. ``test_sdk_from_workflow``) aren't poisoned by
    a leftover credentials file from this module.
    """
    from bifrost.credentials import get_credentials_path
    from src.core.security import authenticate_engine

    authenticate_engine()
    try:
        yield
    finally:
        path = get_credentials_path()
        if path.exists():
            path.unlink()


def _run_bifrost(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke the shipped ``bifrost`` binary via :mod:`subprocess`.

    Sets ``BIFROST_API_URL`` so the CLI's credential resolver points at
    the in-network test API. ``authenticate_engine`` writes the same URL
    into the credentials file, so the SDK matches the right record.
    """
    env = {**os.environ, "BIFROST_API_URL": "http://api:8000"}
    try:
        return subprocess.run(
            ["bifrost", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError as e:
        raise AssertionError(
            "bifrost shim not found on PATH — is /usr/local/bin/bifrost "
            "installed in this image?"
        ) from e


@pytest.mark.e2e
def test_bifrost_on_path(engine_creds) -> None:
    """``which bifrost`` resolves and ``bifrost --help`` runs.

    Smoke check that the ``/usr/local/bin/bifrost`` shim installed in
    ``api/Dockerfile.dev`` is on PATH and dispatches to
    ``python -m bifrost``.
    """
    which = subprocess.run(
        ["which", "bifrost"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert which.returncode == 0, which.stderr
    assert "/bifrost" in which.stdout

    help_result = _run_bifrost(["--help"])
    assert help_result.returncode == 0, help_result.stderr
    assert "bifrost" in help_result.stdout.lower()


@pytest.mark.e2e
def test_files_write_then_read_roundtrip(engine_creds) -> None:
    """Write a file via CLI, read it back, contents match."""
    path = f"e2e/{uuid.uuid4().hex}.txt"
    payload = "hello-from-cli"

    try:
        write = _run_bifrost(["files", "write", path, "--content", payload])
        assert write.returncode == 0, f"write failed: {write.stderr}"

        read = _run_bifrost(["files", "read", path])
        assert read.returncode == 0, f"read failed: {read.stderr}"
        assert read.stdout.rstrip("\n") == payload
    finally:
        _run_bifrost(["files", "delete", path])


@pytest.mark.e2e
def test_files_exists_exit_codes(engine_creds) -> None:
    """``exists`` returns 0 for present, 1 for absent."""
    path = f"e2e/{uuid.uuid4().hex}.txt"
    _run_bifrost(["files", "write", path, "--content", "x"])

    present = _run_bifrost(["files", "exists", path])
    assert present.returncode == 0, present.stderr

    _run_bifrost(["files", "delete", path])
    absent = _run_bifrost(["files", "exists", path])
    assert absent.returncode == 1, (
        f"exists on deleted file should exit 1, got {absent.returncode}: {absent.stdout}"
    )


@pytest.mark.e2e
def test_files_search_finds_written_content(engine_creds) -> None:
    """``search`` hits content written via the CLI.

    If this fails, ``FileIndex`` isn't updated synchronously on
    ``POST /api/files/write``. That's a finding worth surfacing in the
    plan's "Findings" section, not silently weakening the assertion.
    """
    marker = f"BUILDFROST_E2E_MARKER_{uuid.uuid4().hex}"
    path = f"e2e/{uuid.uuid4().hex}.txt"

    try:
        write = _run_bifrost(
            ["files", "write", path, "--content", f"line1\n{marker}\nline3\n"]
        )
        assert write.returncode == 0, write.stderr

        result = _run_bifrost(["files", "--json", "search", marker])
        assert result.returncode == 0, result.stderr
        body = json.loads(result.stdout)

        assert body["total_matches"] >= 1, (
            "search did not find content written via CLI; "
            "FileIndex may be updated asynchronously"
        )
        leaf = path.split("/")[-1]
        assert any(r["file_path"].endswith(leaf) for r in body["results"])
    finally:
        _run_bifrost(["files", "delete", path])
