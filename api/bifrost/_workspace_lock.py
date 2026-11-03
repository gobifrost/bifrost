"""Per-workspace exclusion lock for `bifrost watch` / `bifrost sync`.

Two `bifrost watch` instances pointed at the same workspace, or a `bifrost
sync` running concurrently with a `bifrost watch`, both produce broken
behavior — distinct session_ids that the server can't dedupe, so they ping-
pong each other's writes back as "incoming" events. This module enforces
single-session-per-workspace using an OS-level file lock.

Why an OS lock and not a PID file:

A PID file requires us to detect stale entries (process died without
cleaning up) by checking whether the PID is still alive — racy and gets
fooled by PID reuse. `fcntl.flock` (POSIX) and `msvcrt.locking` (Windows)
are kernel-tracked: when the file descriptor is released — *whatever* the
reason: clean exit, Ctrl-C, SIGKILL, OOM, panic, reboot — the kernel
releases the lock automatically. There is no reaper, no TTL, no stale
state to clean up.

The lock file *also* records PID + command + start time, but as
informational metadata only — the lock itself is the FD, not the file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import stat
import sys
import tempfile
import threading
from datetime import UTC, datetime
from typing import IO, Any, Self

logger = logging.getLogger(__name__)


_LOCK_NAME = ".session.lock"

# In-process registry of currently-held workspace locks. POSIX flock is
# advisory and per-open-file-description: multiple FDs in the same process
# can hold an "exclusive" flock at the same time. That breaks the guarantee
# we want (one session per workspace, period), so we add a process-local
# guard that raises before flock is even attempted. Across processes, flock
# still does the work.
_HELD: set[str] = set()
_HELD_LOCK = threading.Lock()


def _lock_path(workspace: pathlib.Path) -> pathlib.Path:
    digest = hashlib.sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()
    temp_root = pathlib.Path(tempfile.gettempdir())
    if hasattr(os, "getuid"):
        lock_directory = temp_root / f"bifrost-{os.getuid()}" / "workspace-locks"
    else:
        # Windows TEMP is scoped to the current user. Keep a Bifrost-specific
        # child directory so lock files never share the TEMP root directly.
        lock_directory = temp_root / "bifrost" / "workspace-locks"
    return lock_directory / f"{digest}-{_LOCK_NAME}"


def _legacy_lock_path(workspace: pathlib.Path) -> pathlib.Path:
    """Return the pre-private-namespace path used by older POSIX clients."""
    digest = hashlib.sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()
    return (
        pathlib.Path(tempfile.gettempdir())
        / "bifrost"
        / "workspace-locks"
        / f"{digest}-{_LOCK_NAME}"
    )


def _validate_directory(fd: int, path: pathlib.Path) -> None:
    directory_stat = os.fstat(fd)
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise NotADirectoryError(f"workspace lock directory is not a directory: {path}")


def _validate_private_directory(fd: int, path: pathlib.Path) -> None:
    _validate_directory(fd, path)
    directory_stat = os.fstat(fd)
    if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
        raise PermissionError(
            f"workspace lock directory is not owned by the current user: {path}"
        )
    os.fchmod(fd, 0o700)


def _open_posix_lock_file(
    path: pathlib.Path,
    *,
    shared_user_directory: bool = False,
) -> IO[Any]:
    """Open a lock file relative to verified private directories.

    Directory descriptors plus ``O_NOFOLLOW`` keep an attacker from swapping a
    checked path for a symlink between validation and open. The legacy fence
    lives below ``/tmp/bifrost``, which is also a cross-UID test-result mount;
    that shared parent is type-checked but its permissions are not changed.
    Its ``workspace-locks`` child and the lock file remain owner-only.
    """
    user_directory = path.parent.parent
    try:
        user_directory.mkdir(mode=0o700)
    except FileExistsError:
        # The descriptor checks below validate the existing entry before use.
        pass

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    user_fd = os.open(user_directory, directory_flags)
    lock_directory_fd: int | None = None
    file_fd: int | None = None
    try:
        if shared_user_directory:
            _validate_directory(user_fd, user_directory)
        else:
            _validate_private_directory(user_fd, user_directory)
        try:
            os.mkdir(path.parent.name, mode=0o700, dir_fd=user_fd)
        except FileExistsError:
            # The no-follow open and descriptor checks below validate it.
            pass
        lock_directory_fd = os.open(path.parent.name, directory_flags, dir_fd=user_fd)
        _validate_private_directory(lock_directory_fd, path.parent)

        file_flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        file_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        file_fd = os.open(path.name, file_flags, 0o600, dir_fd=lock_directory_fd)
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError(f"workspace lock path is not a regular file: {path}")
        if file_stat.st_uid != os.getuid():
            raise PermissionError(
                f"workspace lock file is not owned by the current user: {path}"
            )
        os.fchmod(file_fd, 0o600)
        fh = os.fdopen(file_fd, "r+", encoding="utf-8")
        file_fd = None
        return fh
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if lock_directory_fd is not None:
            os.close(lock_directory_fd)
        os.close(user_fd)


def _open_lock_file(path: pathlib.Path) -> IO[Any]:
    if os.name == "posix":
        return _open_posix_lock_file(path)

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise OSError(f"workspace lock path must not be a symlink: {path}")
    path.parent.chmod(0o700)
    file_fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError(f"workspace lock path is not a regular file: {path}")
        os.chmod(path, 0o600)
        fh = os.fdopen(file_fd, "r+", encoding="utf-8")
        file_fd = -1
        return fh
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _open_legacy_lock_file(path: pathlib.Path) -> IO[Any]:
    if os.name == "posix":
        return _open_posix_lock_file(path, shared_user_directory=True)
    return _open_lock_file(path)


def _platform_lock(fh: IO[Any]) -> None:
    """Acquire an exclusive non-blocking lock on `fh`. Raises BlockingIOError
    if another process holds it."""
    if sys.platform == "win32":
        import msvcrt

        # LK_NBLCK = exclusive non-blocking. The "1 byte at offset 0" idiom
        # is the standard pattern for advisory locking on Windows.
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        except OSError as e:
            # Windows raises OSError(EACCES/EDEADLK) on contention. Translate
            # to BlockingIOError so callers have one exception type.
            raise BlockingIOError(str(e)) from e
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _read_metadata(
    path: pathlib.Path,
    *,
    legacy: bool = False,
) -> dict[str, Any]:
    """Read informational metadata from the lock file. Returns {} on any
    error — metadata is for the error message only, never for correctness."""
    try:
        opener = _open_legacy_lock_file if legacy else _open_lock_file
        with opener(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


class WorkspaceLockError(RuntimeError):
    """A different bifrost session already holds the workspace lock."""

    def __init__(self, workspace: pathlib.Path, holder: dict[str, Any]) -> None:
        self.workspace = workspace
        self.holder = holder
        super().__init__(self._format())

    def _format(self) -> str:
        pid = self.holder.get("pid", "?")
        command = self.holder.get("command", "?")
        started = self.holder.get("started_at", "")
        when = ""
        if started:
            try:
                # Best-effort: parse the ISO timestamp and emit a friendlier
                # "started 14:32:01" suffix. Fall back to raw string.
                ts = datetime.fromisoformat(started)
                when = f"  (started {ts.astimezone().strftime('%Y-%m-%d %H:%M:%S')})"
            except ValueError:
                when = f"  (started {started})"
        return (
            f"another bifrost session is active in this workspace.\n"
            f"  PID {pid}  bifrost {command}{when}\n"
            f"Stop it first or wait for it to finish."
        )


class WorkspaceLock:
    """Acquire on `__enter__`, release on `__exit__` (or on process exit —
    the kernel cleans up the FD if we crash)."""

    def __init__(self, workspace: pathlib.Path, command: str) -> None:
        self.workspace = workspace.resolve()
        self.command = command
        self.path = _lock_path(self.workspace)
        self.legacy_path = _legacy_lock_path(self.workspace)
        self._fh: IO[Any] | None = None
        self._legacy_fh: IO[Any] | None = None

    def __enter__(self) -> Self:
        # In-process check first. POSIX flock is advisory and lets the
        # same process flock one path twice — without this guard, two
        # sessions in the same Python process would both succeed and
        # ping-pong each other.
        key = str(self.path)
        with _HELD_LOCK:
            if key in _HELD:
                raise WorkspaceLockError(self.workspace, _read_metadata(self.path))
            # Reserve before opening the file so two threads racing
            # through __enter__ can't both observe the empty set.
            _HELD.add(key)

        # Assign the open FD directly to self._fh so the close path is
        # local to this class — `self._fh.close()` runs in `_release()`,
        # which both `__exit__` and the failure-path catch-all invoke.
        # Any failure during enter calls `_release()` to close the FD
        # and roll back the in-process reservation in one place.
        try:
            # POSIX clients released before the private lock namespace use the
            # legacy path. Hold a securely opened fence there for the session
            # so an upgrade cannot overlap an already-running old watch/sync,
            # and an old client cannot start while this client is active.
            if self.legacy_path != self.path:
                self._legacy_fh = _open_legacy_lock_file(self.legacy_path)
                try:
                    _platform_lock(self._legacy_fh)
                except BlockingIOError:
                    raise WorkspaceLockError(
                        self.workspace,
                        _read_metadata(self.legacy_path, legacy=True),
                    )

            # Open read/write without truncation. If someone else holds the
            # lock, their metadata must remain intact. seek+truncate runs only
            # after acquisition.
            self._fh = _open_lock_file(self.path)
            try:
                _platform_lock(self._fh)
            except BlockingIOError:
                raise WorkspaceLockError(self.workspace, _read_metadata(self.path))

            # We hold the lock. Stamp metadata. Failure here doesn't
            # break correctness — the lock is on the FD, not the file —
            # so log and continue.
            metadata = {
                "pid": os.getpid(),
                "command": self.command,
                "started_at": datetime.now(UTC).isoformat(),
            }
            for lock_fh in (self._legacy_fh, self._fh):
                if lock_fh is None:
                    continue
                try:
                    lock_fh.seek(0)
                    lock_fh.truncate()
                    json.dump(metadata, lock_fh)
                    lock_fh.flush()
                except OSError as e:
                    logger.debug(f"could not write lock metadata: {e}")

            return self
        except BaseException:
            self._release()
            raise

    def __exit__(self, *exc: object) -> None:
        self._release()

    def _release(self) -> None:
        # Closing the FD releases the kernel-level lock. The lock file
        # is intentionally NOT deleted — a stale file with no FD holder
        # doesn't block anyone (next acquire claims it and overwrites
        # the metadata). Idempotent: __exit__ on a never-entered lock,
        # or after a failed __enter__, is a no-op.
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError as e:
                logger.debug(f"error closing lock file: {e}")
            self._fh = None
        if self._legacy_fh is not None:
            try:
                self._legacy_fh.close()
            except OSError as e:
                logger.debug(f"error closing legacy lock file: {e}")
            self._legacy_fh = None
        with _HELD_LOCK:
            _HELD.discard(str(self.path))
