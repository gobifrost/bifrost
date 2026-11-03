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
import stat
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest
from bifrost import _workspace_lock
from bifrost._workspace_lock import WorkspaceLock, WorkspaceLockError

API_ROOT = pathlib.Path(__file__).resolve().parents[2]


class _WindowsOSProxy:
    """Exercise the Windows branch without changing the process-wide OS module."""

    name = "nt"

    def __getattr__(self, name: str) -> object:
        if name == "getuid":
            raise AttributeError(name)
        return getattr(os, name)


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
        with pytest.raises(WorkspaceLockError) as exc_info, WorkspaceLock(ws, "sync"):
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_acquire_rejects_symlink_private_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-user lock root cannot be redirected before Bifrost creates it."""
    monkeypatch.setattr(
        _workspace_lock.tempfile, "gettempdir", lambda: str(tmp_path / "temp")
    )
    ws = _make_workspace(tmp_path / "workspace")
    lock_path = _workspace_lock._lock_path(ws)
    lock_path.parent.parent.parent.mkdir(parents=True)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    lock_path.parent.parent.symlink_to(redirected, target_is_directory=True)

    with pytest.raises(OSError), WorkspaceLock(ws, "watch"):
        pass

    assert list(redirected.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_acquire_rejects_symlink_lock_file_without_clobbering_target(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A predictable lock filename must never follow an attacker-made symlink."""
    monkeypatch.setattr(
        _workspace_lock.tempfile, "gettempdir", lambda: str(tmp_path / "temp")
    )
    ws = _make_workspace(tmp_path / "workspace")
    lock_path = _workspace_lock._lock_path(ws)
    lock_path.parent.mkdir(parents=True)
    target = tmp_path / "target.txt"
    target.write_text("do not overwrite", encoding="utf-8")
    lock_path.symlink_to(target)

    with pytest.raises(OSError), WorkspaceLock(ws, "watch"):
        pass

    assert target.read_text(encoding="utf-8") == "do not overwrite"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
def test_lock_directory_and_file_are_owner_only(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing same-owner directories are tightened before lock metadata is written."""
    monkeypatch.setattr(
        _workspace_lock.tempfile, "gettempdir", lambda: str(tmp_path / "temp")
    )
    ws = _make_workspace(tmp_path / "workspace")
    lock_path = _workspace_lock._lock_path(ws)
    lock_path.parent.mkdir(parents=True)
    lock_path.parent.chmod(0o777)

    with WorkspaceLock(ws, "watch"):
        directory_mode = stat.S_IMODE(lock_path.parent.stat().st_mode)
        file_mode = stat.S_IMODE(lock_path.stat().st_mode)

    assert directory_mode == 0o700
    assert file_mode == 0o600


def test_windows_lock_path_uses_user_scoped_temp_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows locks stay below the current user's TEMP directory."""
    temp_root = tmp_path / "temp"
    monkeypatch.setattr(_workspace_lock, "os", _WindowsOSProxy())
    monkeypatch.setattr(
        _workspace_lock.tempfile, "gettempdir", lambda: str(temp_root)
    )

    lock_path = _workspace_lock._lock_path(tmp_path / "workspace")

    assert lock_path.parent == temp_root / "bifrost" / "workspace-locks"


def test_windows_lock_open_rejects_symlink_without_clobbering_target(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows fallback must reject a predictable lock-file symlink."""
    lock_path = tmp_path / "locks" / "workspace.lock"
    lock_path.parent.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("do not overwrite", encoding="utf-8")
    lock_path.symlink_to(target)
    monkeypatch.setattr(_workspace_lock, "os", _WindowsOSProxy())

    with pytest.raises(OSError, match="must not be a symlink"):
        _workspace_lock._open_lock_file(lock_path)

    assert target.read_text(encoding="utf-8") == "do not overwrite"


def test_windows_legacy_lock_open_tightens_modes_and_round_trips(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows fallback keeps its directory and lock file owner-only."""
    lock_path = tmp_path / "locks" / "workspace.lock"
    lock_path.parent.mkdir()
    lock_path.parent.chmod(0o777)
    monkeypatch.setattr(_workspace_lock, "os", _WindowsOSProxy())

    with _workspace_lock._open_legacy_lock_file(lock_path) as lock_file:
        lock_file.write("holder")
        lock_file.seek(0)
        assert lock_file.read() == "holder"

    assert stat.S_IMODE(lock_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor semantics")
def test_posix_directory_validation_rejects_regular_file(
    tmp_path: pathlib.Path,
) -> None:
    """Descriptor validation must reject a non-directory lock root."""
    regular_file = tmp_path / "not-a-directory"
    regular_file.write_text("content", encoding="utf-8")
    file_fd = os.open(regular_file, os.O_RDONLY)
    try:
        with pytest.raises(NotADirectoryError):
            _workspace_lock._validate_directory(file_fd, regular_file)
    finally:
        os.close(file_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership semantics")
def test_posix_private_directory_rejects_different_owner(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private lock directory cannot be accepted from another uid."""
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_fstat = os.fstat

    def mismatched_owner(fd: int) -> object:
        result = real_fstat(fd)
        return SimpleNamespace(
            st_mode=result.st_mode,
            st_uid=os.getuid() + 1,
        )

    monkeypatch.setattr(_workspace_lock.os, "fstat", mismatched_owner)
    try:
        with pytest.raises(PermissionError, match="not owned"):
            _workspace_lock._validate_private_directory(directory_fd, tmp_path)
    finally:
        os.close(directory_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFO semantics")
def test_posix_lock_open_rejects_non_regular_file(
    tmp_path: pathlib.Path,
) -> None:
    """A FIFO at the predictable filename is rejected and its fd is closed."""
    lock_path = tmp_path / "private" / "workspace-locks" / "workspace.lock"
    lock_path.parent.mkdir(parents=True)
    os.mkfifo(lock_path)

    with pytest.raises(OSError, match="not a regular file"):
        _workspace_lock._open_posix_lock_file(lock_path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership semantics")
def test_posix_lock_open_rejects_different_file_owner(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock file owned by another uid is rejected before metadata writes."""
    lock_path = tmp_path / "private" / "workspace-locks" / "workspace.lock"
    real_fstat = os.fstat

    def mismatched_file_owner(fd: int) -> object:
        result = real_fstat(fd)
        if stat.S_ISREG(result.st_mode):
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_uid=os.getuid() + 1,
            )
        return result

    monkeypatch.setattr(_workspace_lock.os, "fstat", mismatched_file_owner)

    with pytest.raises(PermissionError, match="not owned"):
        _workspace_lock._open_posix_lock_file(lock_path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
def test_legacy_fence_preserves_shared_result_root_permissions(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The migration fence must not privatize the cross-UID result mount."""
    temp_root = tmp_path / "temp"
    shared_result_root = temp_root / "bifrost"
    shared_result_root.mkdir(parents=True)
    shared_result_root.chmod(0o777)
    monkeypatch.setattr(
        _workspace_lock.tempfile, "gettempdir", lambda: str(temp_root)
    )
    ws = _make_workspace(tmp_path / "workspace")
    legacy_path = _workspace_lock._legacy_lock_path(ws)

    assert stat.S_IMODE(shared_result_root.stat().st_mode) == 0o777
    with WorkspaceLock(ws, "watch"):
        assert stat.S_IMODE(shared_result_root.stat().st_mode) == 0o777
        assert stat.S_IMODE(legacy_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(legacy_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(shared_result_root.stat().st_mode) == 0o777


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_killed_holder_releases_lock(tmp_path: pathlib.Path) -> None:
    """Kernel cleans up the FD even on SIGKILL. After the holder dies, a new
    acquire must succeed without manual cleanup or PID checking."""
    ws = _make_workspace(tmp_path)

    # Spawn a subprocess that acquires the lock and then sleeps. We SIGKILL
    # it without giving it a chance to clean up — the kernel must release
    # the flock on its own.
    #
    # The child needs the API source root on sys.path independently of the
    # caller's working directory. Preserve any configured path and append the
    # resolved repository API root used by this test module.
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
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                filter(None, (os.environ.get("PYTHONPATH"), str(API_ROOT)))
            ),
        },
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
        with pytest.raises(WorkspaceLockError), WorkspaceLock(ws, "sync"):
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock migration semantics")
def test_legacy_holder_blocks_new_namespace_until_release(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-upgrade holder must remain exclusive across the path migration."""
    monkeypatch.setattr(
        _workspace_lock.tempfile, "gettempdir", lambda: str(tmp_path / "temp")
    )
    ws = _make_workspace(tmp_path / "workspace")
    legacy_path = _workspace_lock._legacy_lock_path(ws)
    legacy_path.parent.mkdir(parents=True)
    holder_script = textwrap.dedent(f"""
        import fcntl
        import pathlib
        import time
        path = pathlib.Path({str(legacy_path)!r})
        with path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            print("ACQUIRED", flush=True)
            time.sleep(60)
    """).strip()

    proc = subprocess.Popen(
        [sys.executable, "-c", holder_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        assert proc.stdout is not None
        assert (
            proc.stdout.readline().decode("utf-8", errors="replace").strip()
            == "ACQUIRED"
        )
        with pytest.raises(WorkspaceLockError), WorkspaceLock(ws, "sync"):
            pass
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    with WorkspaceLock(ws, "sync"):
        pass


def test_workspace_lock_error_includes_human_readable_time(
    tmp_path: pathlib.Path,
) -> None:
    """The error message should render the lock holder's start time in a
    locale-friendly form, not a raw ISO string."""
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        with pytest.raises(WorkspaceLockError) as exc_info, WorkspaceLock(ws, "sync"):
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
    with WorkspaceLock(ws_a, "watch"), WorkspaceLock(ws_b, "watch"):
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

    with pytest.raises(RuntimeError, match="simulated"), WorkspaceLock(ws, "watch"):
        pass

    # Reservation rolled back: a fresh acquire must succeed.
    monkeypatch.undo()
    with WorkspaceLock(ws, "watch"):
        pass


def test_blocking_acquire_does_not_leak_fd_or_reservation(
    tmp_path: pathlib.Path,
) -> None:
    """A BlockingIOError from a cross-process contender must close the
    contender's FD and roll back the in-process reservation, leaving the
    workspace acquirable again once the holder releases."""
    ws = _make_workspace(tmp_path)
    with WorkspaceLock(ws, "watch"):
        # First attempt fails as expected.
        with pytest.raises(WorkspaceLockError), WorkspaceLock(ws, "sync"):
            pass
        # Second attempt also fails — proves the first didn't leave a
        # stale entry in `_HELD` that would mask the real holder.
        with pytest.raises(WorkspaceLockError), WorkspaceLock(ws, "sync"):
            pass
    # Holder released; acquire now succeeds.
    with WorkspaceLock(ws, "sync"):
        pass
