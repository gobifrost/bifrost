r"""Regression tests for Windows path-separator handling in push/sync/watch.

Repo keys (the paths the CLI sends to the platform and uses as known-hash
cache keys) are ALWAYS POSIX ("/"). On Windows, ``str(WindowsPath)`` and
f-string interpolation of a ``WindowsPath`` produce backslashes. If a repo
key is built that way, two things break:

1. The key (e.g. ``workflows\hello.py``) never matches the server's
   ``workflows/hello.py``, so the watch no-op-push guard never fires and the
   same unchanged file re-uploads on every tick — the "saving things multiple
   times" symptom reported on Windows.
2. pathspec ignore globs are "/"-based and silently stop matching.

The production code uses ``Path.relative_to(...).as_posix()`` at every key
site. These tests lock that in. The walker/prefix tests run on any OS (real
files under tmp_path); the separator-conversion test uses ``PureWindowsPath``
to exercise the exact behaviour a Windows filesystem would produce.
"""
from __future__ import annotations

import pathlib

import pytest

from bifrost.cli import _collect_push_files, _detect_repo_prefix


def test_pure_windows_relative_as_posix_has_no_backslashes() -> None:
    # This is the core invariant the fix relies on: as_posix() turns Windows
    # separators into "/" even for a Windows-style path. str() would not.
    win = pathlib.PureWindowsPath(r"C:\Users\dev\ws\workflows\hello.py")
    base = pathlib.PureWindowsPath(r"C:\Users\dev\ws")
    rel = win.relative_to(base)

    assert str(rel) == r"workflows\hello.py"  # the bug, if used directly
    assert rel.as_posix() == "workflows/hello.py"  # what the CLI now uses
    assert "\\" not in rel.as_posix()


def test_collect_push_files_keys_are_posix(tmp_path) -> None:
    nested = tmp_path / "workflows" / "sub"
    nested.mkdir(parents=True)
    (nested / "hello.py").write_text("print('hi')\n")
    (tmp_path / "top.py").write_text("x = 1\n")

    files, _skipped = _collect_push_files(tmp_path, repo_prefix="")

    assert "workflows/sub/hello.py" in files
    assert "top.py" in files
    assert all("\\" not in key for key in files), files


def test_collect_push_files_keys_are_posix_with_prefix(tmp_path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "c.py").write_text("y = 2\n")

    files, _skipped = _collect_push_files(tmp_path, repo_prefix="apps/my-app")

    assert "apps/my-app/a/b/c.py" in files
    assert all("\\" not in key for key in files), files


def test_detect_repo_prefix_is_posix(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "apps" / "my-app"
    target.mkdir(parents=True)

    prefix = _detect_repo_prefix(target)

    assert prefix == "apps/my-app"
    assert "\\" not in prefix


def test_detect_repo_prefix_launch_dir_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _detect_repo_prefix(tmp_path) == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
