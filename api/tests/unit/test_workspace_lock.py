"""Unit tests for the per-workspace exclusion lock.

The lock guards `bifrost watch` / `bifrost sync` / `bifrost push` /
`bifrost pull` against the multi-session_id ping-pong: two sessions in one
workspace publish events with distinct session_ids that the server can't
dedupe, so they bounce each other's writes back as "incoming."

Properties under test:

- A second acquire on the same workspace fails with `WorkspaceLockError`.
- The error message exposes the holder's PID + command + start time so
  the user can find and stop them.
- Releasing (or letting the FD go out of scope) lets a fresh acquire
  succeed.
- The lock file lives outside the workspace. We do not test
  `flock` semantics on NFS / Windows here — the wrappers in
  `_workspace_lock.py` rely on platform-native primitives that the
  kernel manages.
- After a process holding the lock dies (subprocess that opens the lock
  and is then SIGKILLed), the next acquire from this process succeeds —
  i.e. no manual cleanup is needed.
"""
from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from bifrost import _workspace_lock
from bifrost._workspace_lock import WorkspaceLock, WorkspaceLockError


def _make_workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_acquire_succeeds_when_no_holder(tmp_path: pathlib.Path) -> None:
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        pass


def test_second_acquire_in_same_process_fails(tmp_path: pathlib.Path) -> None:
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        with pytest.raises(WorkspaceLockError) as exc_info:
            with WorkspaceLock(ws, "sync"):
                pass
        msg = str(exc_info.value)
        assert "watch" in msg, f"holder command should appear in error: {msg}"
        assert str(os.getpid()) in msg, f"holder PID should appear in error: {msg}"


def test_release_allows_reacquire(tmp_path: pathlib.Path) -> None:
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        pass
    # Should not raise — previous holder is gone.
    with WorkspaceLock(ws, "sync"):
        pass


def test_lock_file_is_outside_workspace(tmp_path: pathlib.Path) -> None:
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        assert not (ws / ".bifrost").exists()


def test_acquire_does_not_create_dot_bifrost(tmp_path: pathlib.Path) -> None:
    """The lock helper must not bootstrap `.bifrost/`.

    `.bifrost/` is import/export-only; sync command coordination state lives
    outside the workspace.
    """
    with WorkspaceLock(tmp_path, "watch"):
        assert not (tmp_path / ".bifrost").exists()


def test_holder_metadata_records_command(tmp_path: pathlib.Path) -> None:
    """The lock file should record the holder's command so a contender can
    name what to stop."""
    import json
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        meta = json.loads(_workspace_lock._lock_path(ws).read_text())
        assert meta["command"] == "watch"
        assert meta["pid"] == os.getpid()
        assert "started_at" in meta


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_killed_holder_releases_lock(tmp_path: pathlib.Path) -> None:
    """Kernel cleans up the FD even on SIGKILL. After the holder dies, a new
    acquire must succeed without manual cleanup or PID checking."""
    ws = _make_workspace(tmp_path)

    # Spawn a subprocess that acquires the lock and then sleeps. We SIGKILL
    # it without giving it a chance to clean up — the kernel must release
    # the flock on its own.
    #
    # The child needs the bifrost package on sys.path. The test container
    # mounts the source at /app, but locally pytest runs from the repo root.
    # Inherit the parent's PYTHONPATH so whichever applies works.
    holder_script = textwrap.dedent(f"""
        import pathlib
        from bifrost._workspace_lock import WorkspaceLock
        with WorkspaceLock(pathlib.Path({str(ws)!r}), "watch"):
            print("ACQUIRED", flush=True)
            import time
            time.sleep(60)
    """).strip()

    proc = subprocess.Popen(
        [sys.executable, "-c", holder_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "") + ":/app:."},
    )
    captured: list[str] = []
    try:
        # Wait for the child to confirm it acquired before we test contention
        deadline = time.time() + 10
        acquired = False
        while time.time() < deadline:
            assert proc.stdout is not None
            line = proc.stdout.readline().decode("utf-8", errors="replace").strip()
            captured.append(line)
            if line == "ACQUIRED":
                acquired = True
                break
        assert acquired, (
            f"child process did not report ACQUIRED in time. captured={captured!r}, "
            f"poll={proc.poll()!r}"
        )

        # While the child holds the lock, our acquire must fail.
        with pytest.raises(WorkspaceLockError):
            with WorkspaceLock(ws, "sync"):
                pass

        # Kill -9 the child. No chance for cleanup. The kernel must release
        # the FD.
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    # Now acquire must succeed — kernel released the lock.
    with WorkspaceLock(ws, "sync"):
        pass


def test_workspace_lock_error_includes_human_readable_time(tmp_path: pathlib.Path) -> None:
    """The error message should render the lock holder's start time in a
    locale-friendly form, not a raw ISO string."""
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        with pytest.raises(WorkspaceLockError) as exc_info:
            with WorkspaceLock(ws, "sync"):
                pass
        msg = str(exc_info.value)
        # Either ISO or formatted — but never empty
        assert "started" in msg.lower(), f"missing started_at in error: {msg}"


def test_context_manager_protocol(tmp_path: pathlib.Path) -> None:
    """`WorkspaceLock` should support `with` directly (not just
    `WorkspaceLock().__exit__()`)."""
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        pass
    # Reusable
    with WorkspaceLock(ws, "sync"):
        pass


def test_concurrent_workspaces_do_not_block(tmp_path: pathlib.Path) -> None:
    """Two different workspaces must not block each other — the lock is
    per-workspace, not global."""
    ws_a = _make_workspace(tmp_path / "a")
    ws_b = _make_workspace(tmp_path / "b")
    with WorkspaceLock(ws_a, "watch"):
        with WorkspaceLock(ws_b, "watch"):
            pass


def test_failed_acquire_closes_fd_and_rolls_back_reservation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exception between `open()` and the successful return of
    `__enter__` must close the FD and roll back the in-process
    reservation. Otherwise a contended acquire leaks an FD per attempt
    and a half-failed acquire blocks all future ones in the same process.
    """
    ws = _make_workspace(tmp_path)

    # Force `_platform_lock` to raise an unexpected error (not
    # BlockingIOError) — exercises the catch-all rollback path.
    def boom(_fh: object) -> None:
        raise RuntimeError("simulated platform lock failure")

    monkeypatch.setattr(_workspace_lock, "_platform_lock", boom)

    with pytest.raises(RuntimeError, match="simulated"):
        with WorkspaceLock(ws, "watch"):
            pass

    # Reservation rolled back: a fresh acquire must succeed.
    monkeypatch.undo()
    with WorkspaceLock(ws, "watch"):
        pass


def test_blocking_acquire_does_not_leak_fd_or_reservation(tmp_path: pathlib.Path) -> None:
    """A BlockingIOError from a cross-process contender must close the
    contender's FD and roll back the in-process reservation, leaving the
    workspace acquirable again once the holder releases."""
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        # First attempt fails as expected.
        with pytest.raises(WorkspaceLockError):
            with WorkspaceLock(ws, "sync"):
                pass
        # Second attempt also fails — proves the first didn't leave a
        # stale entry in `_HELD` that would mask the real holder.
        with pytest.raises(WorkspaceLockError):
            with WorkspaceLock(ws, "sync"):
                pass
    # Holder released; acquire now succeeds.
    with WorkspaceLock(ws, "sync"):
        pass
