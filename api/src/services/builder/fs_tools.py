"""Safe filesystem and archive primitives for the private-Solution builder.

The coding model never gets a shell: every workspace mutation it can request is
one of the operations on :class:`WorkspaceRoot`. This module is the only place
that turns a model-supplied string into a real path, so it is the single
chokepoint for the rules in the 2026-07-25 builder spec ("Model tools"):

- relative paths only — no absolute paths, ``..``, NUL, or platform tricks;
- the canonical parent must stay under the session root;
- symlinks, hardlinks, devices, sockets, and FIFOs are refused;
- file count, per-file, and total-workspace limits are enforced;
- tool output is bounded and writes are atomic.

Archive extraction (:func:`safe_extract_zip`) applies the same rules plus
duplicate/conflicting-member and zip-bomb rejection. Every rejection raises
:class:`WorkspaceViolation` with a terse reason; nothing here logs, so callers
decide what surfaces to the model and what surfaces to operators.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

MIB = 1024 * 1024

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class WorkspaceViolation(ValueError):
    """A workspace operation was refused. The message is the terse reason."""


@dataclass(frozen=True)
class WorkspaceLimits:
    """Resource ceilings for one builder workspace (spec: Resource controls)."""

    max_files: int = 5000
    max_file_bytes: int = 5 * MIB
    max_total_bytes: int = 200 * MIB
    max_read_bytes: int = 256 * 1024


@dataclass(frozen=True)
class SearchHit:
    """One matching line from :meth:`WorkspaceRoot.search_text`."""

    path: str
    line_number: int
    line: str


def _reject_path_syntax(rel_path: str) -> PurePosixPath:
    """Validate the raw string form of a relative path and return it parsed."""
    if not rel_path or not rel_path.strip():
        raise WorkspaceViolation("empty path")
    if "\x00" in rel_path:
        raise WorkspaceViolation("path contains NUL")
    if "\\" in rel_path:
        raise WorkspaceViolation("path contains backslash")
    if _WINDOWS_DRIVE_RE.match(rel_path):
        raise WorkspaceViolation("path contains a drive letter")
    if rel_path.startswith("/"):
        raise WorkspaceViolation("absolute path")

    pure = PurePosixPath(rel_path)
    if pure.is_absolute():
        raise WorkspaceViolation("absolute path")

    parts = pure.parts
    if not parts:
        raise WorkspaceViolation("empty path")
    if any(part == ".." for part in parts):
        raise WorkspaceViolation("path contains '..'")
    return pure


def _is_symlink(path: Path) -> bool:
    return path.is_symlink()


def _matches_glob(rel: str, pattern: str) -> bool:
    """Match a relative path against a glob, honouring ``**/`` as "any depth".

    ``fnmatch`` has no recursive-wildcard concept: ``**/*`` would fail to match
    a file sitting at the workspace root, silently hiding it from search. A
    leading ``**/`` is therefore also tried with the prefix removed.
    """
    if fnmatch(rel, pattern):
        return True
    if pattern.startswith("**/"):
        return fnmatch(rel, pattern[3:])
    return False


class WorkspaceRoot:
    """A single builder session's mutable workspace directory."""

    def __init__(self, root: Path, limits: WorkspaceLimits) -> None:
        self._root = Path(os.path.realpath(root))
        self._limits = limits

    @property
    def root(self) -> Path:
        return self._root

    @property
    def limits(self) -> WorkspaceLimits:
        return self._limits

    # -- path resolution -------------------------------------------------

    def _resolve(self, rel_path: str) -> Path:
        """Turn a model-supplied relative path into a vetted absolute path.

        Every operation goes through here. The canonical *parent* is resolved
        (so a not-yet-existing leaf is fine) and must stay under the canonical
        root; no component inside the root may be a symlink.
        """
        pure = _reject_path_syntax(rel_path)
        candidate = self._root.joinpath(*pure.parts)

        # No component between the root and the leaf may be a symlink, else a
        # realpath check on the parent alone would be satisfied by a link that
        # was planted after an earlier check.
        current = self._root
        for part in pure.parts[:-1]:
            current = current / part
            if _is_symlink(current):
                raise WorkspaceViolation(f"symlinked path component: {part}")

        parent_real = Path(os.path.realpath(candidate.parent))
        if parent_real != self._root and self._root not in parent_real.parents:
            raise WorkspaceViolation("path escapes the workspace root")

        if _is_symlink(candidate):
            raise WorkspaceViolation("path is a symlink")

        return parent_real / pure.parts[-1]

    def resolve_target(self, rel_path: str) -> Path:
        """Vetted absolute path for ``rel_path``, for callers writing new files.

        Used by :func:`safe_extract_zip`, which needs the same validation as the
        model-facing tools but does its own streaming write.
        """
        return self._resolve(rel_path)

    def _stat_regular_file(self, path: Path) -> os.stat_result:
        """Stat an existing path, refusing anything but a single-linked file."""
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise WorkspaceViolation("file not found") from exc
        if stat.S_ISLNK(info.st_mode):
            raise WorkspaceViolation("path is a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceViolation("not a regular file")
        if info.st_nlink > 1:
            raise WorkspaceViolation("file has multiple hardlinks")
        return info

    # -- introspection ---------------------------------------------------

    def _iter_files(self) -> list[Path]:
        """Every regular, non-symlinked file under the root, sorted."""
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self._root):
            here = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if not (here / d).is_symlink())
            for name in sorted(filenames):
                path = here / name
                if path.is_symlink() or not path.is_file():
                    continue
                found.append(path)
        return found

    def _usage(self) -> tuple[int, int]:
        """(file count, total bytes) currently stored in the workspace."""
        total = 0
        paths = self._iter_files()
        for path in paths:
            total += path.stat().st_size
        return len(paths), total

    def list_files(self) -> list[str]:
        """Relative paths of every workspace file, sorted and count-bounded."""
        paths = self._iter_files()
        if len(paths) > self._limits.max_files:
            raise WorkspaceViolation("workspace exceeds max file count")
        return sorted(str(p.relative_to(self._root)) for p in paths)

    # -- reads -----------------------------------------------------------

    def read_file(self, rel: str) -> tuple[bytes, bool]:
        """Read a file, capped at ``max_read_bytes``.

        Returns ``(content, truncated)`` where ``truncated`` is True when the
        file was larger than the read cap.
        """
        path = self._resolve(rel)
        info = self._stat_regular_file(path)
        cap = self._limits.max_read_bytes
        with open(path, "rb") as handle:
            data = handle.read(cap)
        return data, info.st_size > cap

    def search_text(
        self,
        pattern: str,
        rel_glob: str = "**/*",
        max_hits: int = 200,
    ) -> list[SearchHit]:
        """Regex-search matching files, returning at most ``max_hits`` lines."""
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise WorkspaceViolation(f"invalid search pattern: {exc}") from exc

        hits: list[SearchHit] = []
        for path in self._iter_files():
            rel = str(path.relative_to(self._root))
            if not _matches_glob(rel, rel_glob):
                continue
            if path.stat().st_size > self._limits.max_read_bytes:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(
                        SearchHit(
                            path=rel,
                            line_number=number,
                            line=line[: self._limits.max_read_bytes],
                        )
                    )
                    if len(hits) >= max_hits:
                        return hits
        return hits

    # -- writes ----------------------------------------------------------

    def write_file(self, rel: str, data: bytes) -> None:
        """Atomically write ``data``, enforcing per-file and workspace limits."""
        if len(data) > self._limits.max_file_bytes:
            raise WorkspaceViolation("file exceeds per-file byte limit")

        path = self._resolve(rel)

        existing_size = 0
        replacing = False
        if path.exists() or path.is_symlink():
            info = self._stat_regular_file(path)
            existing_size = info.st_size
            replacing = True

        count, total = self._usage()
        if not replacing and count + 1 > self._limits.max_files:
            raise WorkspaceViolation("workspace exceeds max file count")
        if total - existing_size + len(data) > self._limits.max_total_bytes:
            raise WorkspaceViolation("workspace exceeds total byte limit")

        _atomic_write(path, data)

    def delete_file(self, rel: str) -> None:
        """Delete a single regular file."""
        path = self._resolve(rel)
        self._stat_regular_file(path)
        path.unlink()

    def make_directory(self, rel: str) -> None:
        """Create a directory (and any missing parents) inside the workspace."""
        path = self._resolve(rel)
        if path.exists():
            if not path.is_dir():
                raise WorkspaceViolation("path exists and is not a directory")
            return
        path.mkdir(parents=True)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a same-directory temp file + rename.

    A crash or a full disk mid-write leaves the temp file, never a torn target.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False)
    tmp_path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _validate_member_name(name: str) -> PurePosixPath:
    """Validate an archive member name with the same rules as tool paths."""
    if name.endswith("/"):
        raise WorkspaceViolation(f"directory member not allowed: {name}")
    return _reject_path_syntax(name)


def _is_symlink_member(info: zipfile.ZipInfo) -> bool:
    """True when the member's external attrs carry the S_IFLNK mode bits."""
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def safe_extract_zip(
    zip_path: Path,
    dest_root: Path,
    limits: WorkspaceLimits,
) -> list[str]:
    """Extract ``zip_path`` into ``dest_root``, refusing hostile archives.

    Members are validated up front (names, symlinks, duplicates, declared
    sizes, count) and then streamed one at a time with a running byte budget,
    so a lying ``file_size`` header cannot smuggle in an oversized expansion.
    Returns the extracted relative paths, sorted.
    """
    root = WorkspaceRoot(dest_root, limits)

    with zipfile.ZipFile(zip_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]

        if len(infos) > limits.max_files:
            raise WorkspaceViolation("archive exceeds max file count")

        seen: dict[str, str] = {}
        declared_total = 0
        for info in infos:
            pure = _validate_member_name(info.filename)
            if _is_symlink_member(info):
                raise WorkspaceViolation(f"symlink member not allowed: {info.filename}")
            key = str(pure).lower()
            if key in seen:
                raise WorkspaceViolation(
                    f"duplicate member path: {info.filename} vs {seen[key]}"
                )
            seen[key] = info.filename
            if info.file_size > limits.max_file_bytes:
                raise WorkspaceViolation(
                    f"member exceeds per-file byte limit: {info.filename}"
                )
            declared_total += info.file_size
            if declared_total > limits.max_total_bytes:
                raise WorkspaceViolation("archive exceeds total byte limit")

        extracted: list[str] = []
        written_total = 0
        for info in infos:
            target = root.resolve_target(info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            written_total += _stream_member(
                archive, info, target, limits, written_total
            )
            extracted.append(str(target.relative_to(root.root)))

    return sorted(extracted)


def _stream_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    target: Path,
    limits: WorkspaceLimits,
    written_total: int,
) -> int:
    """Stream one member to ``target`` atomically; return bytes written."""
    handle = tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".tmp-", delete=False
    )
    tmp_path = Path(handle.name)
    written = 0
    try:
        with handle, archive.open(info) as source:
            while True:
                try:
                    chunk = source.read(64 * 1024)
                except zipfile.BadZipFile as exc:
                    # A falsified uncompressed-size header desynchronises the
                    # stream from its CRC, which surfaces here. It is a hostile
                    # archive like any other, so it gets the same reason type.
                    raise WorkspaceViolation(
                        f"corrupt member stream: {info.filename}"
                    ) from exc
                if not chunk:
                    break
                written += len(chunk)
                if written > limits.max_file_bytes:
                    raise WorkspaceViolation(
                        f"member exceeds per-file byte limit: {info.filename}"
                    )
                if written_total + written > limits.max_total_bytes:
                    raise WorkspaceViolation("archive exceeds total byte limit")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return written
