"""Native Azure Blob workspace acceleration; no symlinks, no source-URL copy."""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Iterable
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory, TemporaryFile
from typing import BinaryIO

from src.config import Settings
from src.core.storage import object_pages
from src.core.storage.azure import AzureObjectStore
from src.core.storage.contracts import (
    ObjectStore, ObjectVersion, SnapshotGuard, StorageConfigurationError, StorageError, WriteOptions,
)


class AzureWorkspaceChanged(RuntimeError):
    """The remote snapshot changed while it was being transferred."""


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
    parts = PurePosixPath(name).parts
    if (
        not parts
        or name.startswith("/")
        or "\\" in name
        or any(part in {".", ".."} or ":" in part for part in parts)
        or PurePosixPath(name).as_posix() != name
    ):
        raise ValueError("Unsafe workspace object path")
    target = root.joinpath(*parts)
    if any(path.is_symlink() for path in (root, target, *target.parents)):
        raise ValueError("Workspace mirror does not follow symbolic links")
    return target


def _files(root: Path) -> dict[str, Path]:
    if root.is_symlink():
        raise ValueError("Workspace mirror does not follow symbolic links")
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Workspace mirror does not follow symbolic links")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            result[name] = _safe_path(root, name)
    return result


class AzureWorkspaceMirror:
    follows_symlinks = False

    def __init__(self, settings: Settings):
        self._settings = settings
        namespace = settings.storage_bucket
        if not namespace:
            raise StorageConfigurationError("Workspace storage container is not configured")
        self._namespace = namespace
        # A hint for this mirror instance only. An opaque remote version and a
        # local content hash must both match before a transfer can be omitted.
        self._cache_scope: tuple[str | None, str, str] | None = None
        self._versions: dict[str, tuple[str, str]] = {}

    def _prefix(self, prefix: str) -> str:
        if not prefix or not prefix.endswith("/") or prefix.startswith("/") or any(
            part in {".", ".."} for part in prefix.split("/")
        ) or "\\" in prefix or PurePosixPath(prefix).as_posix() + "/" != prefix:
            raise ValueError("Invalid workspace object prefix")
        return prefix

    def _cached_versions(self, prefix: str) -> dict[str, tuple[str, str]]:
        scope = (self._settings.azure_blob_account_url, self._namespace, prefix)
        return self._versions if self._cache_scope == scope else {}

    def _remember_versions(self, prefix: str, versions: dict[str, tuple[str, str]]) -> None:
        self._cache_scope = (self._settings.azure_blob_account_url, self._namespace, prefix)
        self._versions = versions

    @staticmethod
    def _hash_path(path: Path) -> str:
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()

    @staticmethod
    def _copy_snapshot(path: Path, destination: BinaryIO) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024**2):
                destination.write(chunk)
                digest.update(chunk)
        destination.seek(0)
        return digest.hexdigest()

    @asynccontextmanager
    async def _upload_snapshot(self, path: Path) -> AsyncIterator[tuple[BinaryIO, str]]:
        with TemporaryFile() as content:
            copying = asyncio.create_task(
                asyncio.to_thread(self._copy_snapshot, path, content)
            )
            try:
                digest = await asyncio.shield(copying)
                yield content, digest
            finally:
                # A cancelled owner must drain its thread before closing the
                # snapshot that thread may still be writing. Completed copies
                # need no new cancellation point during normal cleanup.
                if not copying.done():
                    draining = asyncio.gather(copying, return_exceptions=True)
                    while not draining.done():
                        try:
                            await asyncio.shield(draining)
                        except asyncio.CancelledError:
                            # Preserve the original cancellation, but defer any
                            # further requests until the file is no longer in use.
                            pass

    async def _listing(self, store: ObjectStore, prefix: str) -> dict[str, ObjectVersion]:
        result = {}
        async for page in object_pages(store, prefix):
            for item in page.objects:
                if not item.key.startswith(prefix):
                    raise StorageError("backend_error", provider_code="WorkspacePrefixEscape")
                result[item.key[len(prefix):]] = item.version
        return result

    async def download(self, prefix: str, target: Path) -> None:
        prefix = self._prefix(prefix)
        target.mkdir(parents=True, exist_ok=True)
        local = _files(target)
        async with AzureObjectStore.from_settings(self._settings) as store:
            snapshot = await self._listing(store, prefix)
            cached = self._cached_versions(prefix)
            versions: dict[str, tuple[str, str]] = {}
            retained: set[str] = set()
            # Validate the complete remote tree before touching local files.
            for name in snapshot:
                _safe_path(target, name)
                if any(
                    parent.as_posix() in snapshot
                    for parent in PurePosixPath(name).parents
                ):
                    raise ValueError("Conflicting workspace file and directory paths")
            # Stage all bytes first: a failed download leaves the local tree intact.
            with TemporaryDirectory(
                # The target itself can be a mounted volume. A sibling under
                # target.parent can be on another device and cannot be renamed
                # atomically into the workspace.
                prefix=".bifrost-blob-", dir=target
            ) as temporary:
                staging = Path(temporary)
                if any(PurePosixPath(name).parts[0] == staging.name for name in snapshot):
                    raise ValueError("Workspace object collides with download staging")
                semaphore = asyncio.Semaphore(8)

                async def download(name: str, version: ObjectVersion) -> None:
                    async with semaphore:
                        await download_file(name, version)

                async def download_file(name: str, version: ObjectVersion) -> None:
                    if name in local and name in cached and cached[name][0] == version:
                        digest = await asyncio.to_thread(self._hash_path, local[name])
                        if cached[name][1] == digest:
                            versions[name] = (version, digest)
                            retained.add(name)
                            return
                    async with store.open_read(prefix + name) as read:
                        if read.info.version != version:
                            raise AzureWorkspaceChanged(
                                "Workspace object changed during download"
                            )
                        path = _safe_path(staging, name)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        digest = hashlib.sha256()
                        with path.open("wb") as output:
                            async for chunk in read.body.iter_chunks():
                                output.write(chunk)
                                digest.update(chunk)
                        versions[name] = (version, digest.hexdigest())

                await _run_transfers(
                    download(name, version) for name, version in snapshot.items()
                )
                if snapshot != await self._listing(store, prefix):
                    raise AzureWorkspaceChanged("Workspace changed during download")
                for name, path in local.items():
                    if name not in snapshot:
                        path.unlink()
                # Remove stale empty directories, including file/directory transitions.
                for path in sorted(
                    target.rglob("*"), key=lambda path: len(path.parts), reverse=True
                ):
                    if path == staging or staging in path.parents:
                        continue
                    if path.is_dir() and not any(path.iterdir()):
                        path.rmdir()
                for name in snapshot:
                    if name in retained:
                        continue
                    destination = _safe_path(target, name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    (staging / name).replace(destination)
                self._remember_versions(prefix, versions)

    async def upload(
        self, source: Path, prefix: str, *, validate_snapshot: SnapshotGuard | None = None,
    ) -> None:
        prefix = self._prefix(prefix)
        if not source.is_dir():
            raise ValueError("Workspace source directory does not exist")
        local = _files(source)
        async with AzureObjectStore.from_settings(self._settings) as store:
            snapshot = await self._listing(store, prefix)
            if validate_snapshot is not None:
                validate_snapshot(snapshot)
            expected: dict[str, ObjectVersion] = {}
            cached = self._cached_versions(prefix)
            versions: dict[str, tuple[str, str]] = {}
            semaphore = asyncio.Semaphore(8)

            async def upload(name: str, path: Path) -> None:
                async with semaphore:
                    await upload_file(name, path)

            async def upload_file(name: str, path: Path) -> None:
                if (
                    name in snapshot
                    and name in cached
                    and cached[name][0] == snapshot[name]
                ):
                    digest = await asyncio.to_thread(self._hash_path, path)
                    if cached[name][1] == digest:
                        expected[name] = snapshot[name]
                        versions[name] = (snapshot[name], digest)
                        return
                options = (
                    WriteOptions(expected_version=snapshot[name])
                    if name in snapshot
                    else WriteOptions(create_only=True)
                )
                # The cache witness must hash the bytes consumed by the SDK,
                # not an earlier read of a mutable local path.
                async with self._upload_snapshot(path) as (content, digest):
                    written = await store.write(prefix + name, content.read(), options=options)
                expected[name] = written.version
                versions[name] = (written.version, digest)

            await _run_transfers(upload(name, path) for name, path in local.items())
            for name, version in snapshot.items():
                if name not in local:
                    await store.delete(prefix + name, expected_version=version)
            if expected != await self._listing(store, prefix):
                raise AzureWorkspaceChanged("Workspace changed during upload")
            self._remember_versions(prefix, versions)

    async def delete(self, prefix: str) -> None:
        prefix = self._prefix(prefix)
        async with AzureObjectStore.from_settings(self._settings) as store:
            for name, version in (await self._listing(store, prefix)).items():
                await store.delete(prefix + name, expected_version=version)
            if await self._listing(store, prefix):
                raise AzureWorkspaceChanged("Checkpoint changed during deletion")
