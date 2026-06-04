"""Headless / non-interactive operability of the sync & deploy CLI paths.

Criterion 17 (Solutions success criteria): every CLI path the deploy/dev/test
loop depends on must run unattended — no TUI, no interactive prompt — so the
whole create → deploy → run → verify flow can execute in a script or CI.

These tests invoke the CLI as a subprocess with ``stdin`` closed
(``subprocess.DEVNULL``), which is the strongest proxy for "headless": a TUI or
``input()`` prompt either hangs (caught by the timeout) or errors on EOF.

The CLI under test is a *source* install (``__version__`` is ``0.0.0+source``),
so ``_check_cli_version`` short-circuits and no network call is made for
``--help``.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # api/


def _run(args: list[str], cwd: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m bifrost <args>`` with stdin closed.

    cwd defaults to a neutral dir; env inherits the caller's so the source
    install resolves. ``PYTHONPATH`` includes the api/ root so ``bifrost`` is
    importable without an editable install.
    """
    run_env = {**os.environ, **(env or {})}
    run_env["PYTHONPATH"] = os.pathsep.join(
        [str(_REPO_ROOT), run_env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, "-m", "bifrost", *args],
        cwd=cwd,
        env=run_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_sync_help_advertises_noninteractive(tmp_path: pathlib.Path) -> None:
    """``bifrost sync --help`` documents a non-interactive escape (--yes/-y)."""
    r = _run(["sync", "--help"], cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    out = r.stdout.lower()
    assert "--yes" in out or "-y" in out or "non-interactive" in out, r.stdout


def test_push_help_advertises_noninteractive(tmp_path: pathlib.Path) -> None:
    """``bifrost push --help`` documents the non-interactive escape too."""
    r = _run(["push", "--help"], cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    out = r.stdout.lower()
    assert "--yes" in out or "-y" in out or "non-interactive" in out, r.stdout


def test_yes_flag_is_accepted_by_sync_parser(tmp_path: pathlib.Path) -> None:
    """``--yes`` is a recognized option (no "Unknown option" rejection).

    We point sync at an empty dir against an unreachable API. The parser must
    accept ``--yes`` (exit != "Unknown option"); whatever happens after
    (auth/connection failure) is fine — we only assert the flag parsed.
    """
    r = _run(
        ["sync", str(tmp_path), "--yes"],
        cwd=str(tmp_path),
        env={"BIFROST_API_URL": "http://127.0.0.1:1"},  # nothing listening
    )
    assert "Unknown option" not in r.stdout, r.stdout
    assert "Unknown option" not in r.stderr, r.stderr


def test_noninteractive_env_var_is_honored(tmp_path: pathlib.Path) -> None:
    """``BIFROST_NONINTERACTIVE=1`` does not crash the parser/dispatch."""
    r = _run(
        ["sync", str(tmp_path)],
        cwd=str(tmp_path),
        env={"BIFROST_NONINTERACTIVE": "1", "BIFROST_API_URL": "http://127.0.0.1:1"},
    )
    assert "Unknown option" not in r.stdout, r.stdout
    assert "Unknown option" not in r.stderr, r.stderr


def test_sync_use_tui_headless_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    """The single TUI gate is False whenever headless, True only on an
    unforced TTY. This guards BOTH the selection TUI and the progress TUI
    (which blocks on "press Enter" for errors) — see _sync_use_tui."""
    from bifrost.cli import _sync_use_tui

    monkeypatch.delenv("BIFROST_NONINTERACTIVE", raising=False)

    # Unforced TTY → interactive.
    assert _sync_use_tui(force=False, is_tty=True) is True
    # --yes/-y on a real TTY → headless (the bug Codex caught: progress TUI
    # must not run here, or an error would block on Enter).
    assert _sync_use_tui(force=True, is_tty=True) is False
    # No TTY → headless regardless of force.
    assert _sync_use_tui(force=False, is_tty=False) is False
    assert _sync_use_tui(force=True, is_tty=False) is False

    # BIFROST_NONINTERACTIVE=1 forces headless even on a TTY.
    monkeypatch.setenv("BIFROST_NONINTERACTIVE", "1")
    assert _sync_use_tui(force=False, is_tty=True) is False


@pytest.mark.xfail(reason="deploy command implemented in Sub-plan 1", strict=False)
def test_deploy_help_is_noninteractive(tmp_path: pathlib.Path) -> None:
    """``bifrost deploy --help`` exists and advertises a non-interactive contract.

    Anchored here per the plan; the ``deploy`` command lands in Sub-plan 1, at
    which point the ``xfail`` becomes an ``xpass`` and the marker is removed.
    """
    r = _run(["deploy", "--help"], cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    out = r.stdout.lower()
    assert "--yes" in out or "non-interactive" in out, r.stdout
