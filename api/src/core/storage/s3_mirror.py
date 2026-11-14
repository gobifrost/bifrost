"""Native S3 workspace transfers over the owned object-store contract."""

import asyncio
import hashlib
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Iterable
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from src.config import Settings
from src.core.storage import object_pages
from src.core.storage.contracts import (
    ObjectSummary,
    ObjectVersion,
    SnapshotGuard,
    StorageConfigurationError,
    StorageError,
    WriteOptions,
)
from src.core.storage.s3 import S3ObjectStore

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 8 * 1024 * 1024
_SINGLE_PUT_LIMIT = 8 * 1024 * 1024
_TRANSFER_CONCURRENCY = 8


class S3WorkspaceChanged(RuntimeError):
    """The remote prefix changed while it was being transferred."""


async def _run_transfers(transfers: Iterable[Awaitable[None]]) -> None:
    tasks = [asyncio.create_task(transfer) for transfer in transfers]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _safe_path(root: Path, name: str) -> Path:
    """Resolve a remote object name under a local root.

    Only the name shape is validated: traversal above the root is refused.
    Symbolic links are deliberately followed here — this mirror publishes and
    restores through them (follows_symlinks), matching the previous CLI sync.
    """
    parts = PurePosixPath(name).parts
    if (
        not parts
        or name.startswith("/")
        or "\\" in name
        or any(part in {".", ".."} for part in parts)
        or PurePosixPath(name).as_posix() != name
    ):
        raise ValueError("Unsafe workspace object path")
    return root.joinpath(*parts)


def _files(root: Path) -> dict[str, Path]:
    """Enumerate workspace files, following symbolic links like the CLI did.

    Every distinct local path publishes under its own key, including paths
    through redundant links. Descent stops only at genuine cycles — a
    directory that already appears on the current walk path.
    """
    result: dict[str, Path] = {}
    trail: list[tuple[int, int]] = []
    for directory, directories, names in os.walk(root, followlinks=True):
        current = Path(directory)
        try:
            identity = (current.stat().st_dev, current.stat().st_ino)
        except OSError as error:
            raise ValueError(f"Cannot traverse workspace directory {current}") from error
        depth = len(current.relative_to(root).parts)
        del trail[depth:]
        if identity in trail:
            logger.debug("Skipping workspace symlink cycle at %s", current)
            directories.clear()
            continue
        trail.append(identity)
        for name in names:
            path = current / name
            if path.is_symlink() and not path.exists():
                logger.debug("Skipping dangling workspace symlink %s", path)
                continue
            if path.is_file():
                result[path.relative_to(root).as_posix()] = _safe_path(
                    root, path.relative_to(root).as_posix()
                )
    return result


def _md5_path(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        while chunk := source.read(1024**2):
            digest.update(chunk)
    return digest.hexdigest()


async def _read_chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as source:
        while True:
            chunk = await asyncio.to_thread(source.read, _CHUNK_SIZE)
            if not chunk:
                return
            yield chunk


class S3WorkspaceMirror:
    follows_symlinks = True

    def __init__(self, settings: Settings):
        self._settings = settings
        if not settings.s3_bucket:
            raise StorageConfigurationError("Workspace storage bucket is not configured")

    def _prefix(self, prefix: str) -> str:
        if not prefix or not prefix.endswith("/") or prefix.startswith("/") or any(
            part in {".", ".."} for part in prefix.split("/")
        ) or "\\" in prefix or PurePosixPath(prefix).as_posix() + "/" != prefix:
            raise ValueError("Invalid workspace object prefix")
        return prefix

    async def _listing(self, store: S3ObjectStore, prefix: str) -> dict[str, ObjectSummary]:
        result = {}
        async for page in object_pages(store, prefix):
            for item in page.objects:
                if not item.key.startswith(prefix):
                    raise StorageError("backend_error", provider_code="WorkspacePrefixEscape")
                result[item.key[len(prefix):]] = item
        return result

    @staticmethod
    def _remote_timestamp(item: ObjectSummary) -> float:
        if item.modified_at is None:
            raise StorageError("backend_error", provider_code="MissingWorkspaceTimestamp")
        return item.modified_at.timestamp()

    async def _unchanged(self, path: Path, item: ObjectSummary) -> bool:
        """Whether the local file already matches the remote object.

        Size and whole-second mtime are the fast path. A size-and-mtime match
        is ambiguous (same-size edits within one second), so single-part
        objects fall back to an MD5-versus-ETag comparison. Multipart ETags
        cannot be verified locally and always transfer, as before.
        """
        if path.is_symlink() or not path.is_file():
            return False
        if path.stat().st_size != item.size:
            return False
        if int(path.stat().st_mtime) != int(self._remote_timestamp(item)):
            return False
        if "-" in item.version:
            return False
        return await asyncio.to_thread(_md5_path, path) == item.version

    async def download(self, prefix: str, target: Path) -> None:
        clean = self._prefix(prefix)
        target.mkdir(parents=True, exist_ok=True)
        local = _files(target)
        async with S3ObjectStore.from_settings(self._settings) as store:
            snapshot = await self._listing(store, clean)
            for name in snapshot:
                _safe_path(target, name)
                if any(
                    parent.as_posix() in snapshot
                    for parent in PurePosixPath(name).parents
                ):
                    raise ValueError("Conflicting workspace file and directory paths")
            with TemporaryDirectory(prefix=".bifrost-s3-", dir=target) as temporary:
                staging = Path(temporary)
                if any(PurePosixPath(name).parts[0] == staging.name for name in snapshot):
                    raise ValueError("Workspace object collides with download staging")
                semaphore = asyncio.Semaphore(_TRANSFER_CONCURRENCY)
                retained: set[str] = set()

                async def download(name: str, item: ObjectSummary) -> None:
                    async with semaphore:
                        await self._download_file(store, clean, staging, retained, local.get(name), name, item)

                await _run_transfers(
                    download(name, item) for name, item in snapshot.items()
                )
                if snapshot.keys() != (await self._listing(store, clean)).keys():
                    raise S3WorkspaceChanged("Workspace changed during download")
                for name, path in local.items():
                    if name not in snapshot:
                        if path.is_symlink() or path.is_file():
                            path.unlink()
                        # Directories vanish below once they are empty.
                for path in sorted(
                    target.rglob("*"), key=lambda path: len(path.parts), reverse=True
                ):
                    if path == staging or staging in path.parents:
                        continue
                    if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
                        path.rmdir()
                for name in snapshot:
                    if name in retained:
                        continue
                    destination = _safe_path(target, name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    (staging / name).replace(destination)

    async def _download_file(
        self, store: S3ObjectStore, prefix: str, staging: Path,
        retained: set[str], path: Path | None, name: str, item: ObjectSummary,
    ) -> None:
        timestamp = self._remote_timestamp(item)
        if path is not None and await self._unchanged(path, item):
            os.utime(path, (timestamp, timestamp))
            retained.add(name)
            return
        async with store.open_read(prefix + name) as read:
            if read.info.version != item.version or read.info.size != item.size:
                raise S3WorkspaceChanged("Workspace object changed during download")
            staged = _safe_path(staging, name)
            staged.parent.mkdir(parents=True, exist_ok=True)
            with staged.open("wb") as output:
                async for chunk in read.body.iter_chunks():
                    output.write(chunk)
        os.utime(staged, (timestamp, timestamp))

    async def upload(
        self, source: Path, prefix: str, *, validate_snapshot: SnapshotGuard | None = None,
    ) -> None:
        clean = self._prefix(prefix)
        if not source.is_dir():
            raise ValueError("Workspace source directory does not exist")
        local = _files(source)
        async with S3ObjectStore.from_settings(self._settings) as store:
            snapshot = await self._listing(store, clean)
            if validate_snapshot is not None:
                validate_snapshot({name: item.version for name, item in snapshot.items()})
            expected: dict[str, ObjectVersion] = {}
            semaphore = asyncio.Semaphore(_TRANSFER_CONCURRENCY)

            async def upload(name: str, path: Path) -> None:
                async with semaphore:
                    await self._upload_file(store, clean, expected, snapshot.get(name), name, path)

            await _run_transfers(upload(name, path) for name, path in local.items())
            for name, item in snapshot.items():
                if name not in local:
                    await store.delete(clean + name, expected_version=item.version)
            if expected != {
                name: item.version for name, item in (await self._listing(store, clean)).items()
            }:
                raise S3WorkspaceChanged("Workspace changed during upload")

    async def _upload_file(
        self, store: S3ObjectStore, prefix: str, expected: dict[str, ObjectVersion],
        remote: ObjectSummary | None, name: str, path: Path,
    ) -> None:
        if remote is not None and await self._upload_skippable(path, remote):
            expected[name] = remote.version
            return
        options = (
            WriteOptions(expected_version=remote.version)
            if remote is not None
            else WriteOptions(create_only=True)
        )
        if path.stat().st_size <= _SINGLE_PUT_LIMIT:
            content = await asyncio.to_thread(path.read_bytes)
            written = await store.write(prefix + name, content, options=options)
        else:
            written = await store.write_stream(prefix + name, _read_chunks(path), options=options)
        expected[name] = written.version

    async def _upload_skippable(self, path: Path, remote: ObjectSummary) -> bool:
        if path.stat().st_size != remote.size:
            return False
        if "-" not in remote.version:
            return await asyncio.to_thread(_md5_path, path) == remote.version
        return int(path.stat().st_mtime) <= int(self._remote_timestamp(remote))

    async def delete(self, prefix: str) -> None:
        clean = self._prefix(prefix)
        async with S3ObjectStore.from_settings(self._settings) as store:
            keys = [clean + name for name in await self._listing(store, clean)]
            await store.delete_many(keys)
            if await self._listing(store, clean):
                raise S3WorkspaceChanged("Checkpoint changed during deletion")
