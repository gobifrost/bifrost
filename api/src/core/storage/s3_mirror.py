"""S3 workspace transfers through the owned object-store transport."""

import asyncio
import hashlib
import mimetypes
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import aclosing
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import TypeVar

from src.config import Settings
from src.core.storage import object_pages
from src.core.storage.contracts import ContentProperties, ObjectSummary, SnapshotGuard, StorageError, WriteOptions
from src.core.storage.s3 import S3ObjectStore
from src.core.storage.s3_transfers import STREAM_PART_SIZE


_LocalResult = TypeVar("_LocalResult")


async def _local(operation: Callable[[], _LocalResult]) -> _LocalResult:
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except BaseException:
        draining = asyncio.gather(task, return_exceptions=True)
        while not draining.done():
            try:
                await asyncio.shield(draining)
            except asyncio.CancelledError:
                # A worker thread may still own an open workspace file.
                pass
        raise


async def _transfers(operations: Iterable[Awaitable[None]]) -> None:
    tasks = [asyncio.create_task(operation) for operation in operations]
    running = asyncio.gather(*tasks)
    try:
        await asyncio.shield(running)
    except BaseException:
        for task in tasks:
            task.cancel()
        draining = asyncio.gather(*tasks, running, return_exceptions=True)
        while not draining.done():
            try:
                await asyncio.shield(draining)
            except asyncio.CancelledError:
                # Keep the repository owner until every transfer and body closes.
                pass
        raise


def _prefix(value: str) -> str:
    if not value or not value.endswith("/") or value.startswith("/") or any(
        part in {".", ".."} for part in value.split("/")
    ) or "\\" in value or PurePosixPath(value).as_posix() + "/" != value:
        raise ValueError("Invalid workspace object prefix")
    return value


def _path(root: Path, name: str) -> Path:
    if not name or name.startswith("/") or any(
        part in {".", ".."} for part in name.split("/")
    ) or PurePosixPath(name).as_posix() != name:
        raise ValueError("Unsafe workspace object path")
    return root.joinpath(*PurePosixPath(name).parts)


def _files(root: Path) -> dict[str, Path]:
    # S3 sync has always followed local symlinks, including directory links.
    result = {}
    def unreadable(error: OSError) -> None:
        raise error

    for directory, directories, files in os.walk(root, followlinks=True, onerror=unreadable):
        current = Path(directory)
        for name in directories:
            child = current / name
            if child.is_symlink() and child.resolve() in {current.resolve(), *(parent.resolve() for parent in current.parents)}:
                raise ValueError("Workspace symbolic link forms a directory cycle")
        for name in files:
            path = Path(directory) / name
            if path.is_symlink() and not path.exists():
                raise ValueError("Workspace symbolic link target does not exist")
            if path.is_file():
                result[path.relative_to(root).as_posix()] = path
    return result


def _matches(path: Path, item: ObjectSummary) -> bool:
    if path.stat().st_size != item.size:
        return False
    single = hashlib.md5(usedforsecurity=False)
    parts = hashlib.md5(usedforsecurity=False)
    count = 0
    with path.open("rb") as content:
        while chunk := content.read(STREAM_PART_SIZE):
            single.update(chunk)
            parts.update(hashlib.md5(chunk, usedforsecurity=False).digest())
            count += 1
    # Preserve incremental sync for standard single/multipart S3 ETags. Opaque
    # provider/encryption ETags cannot prove content identity and must transfer.
    return item.version in {single.hexdigest(), f"{parts.hexdigest()}-{count}"}


class S3WorkspaceMirror:
    follows_symlinks = True

    def __init__(self, settings: Settings):
        self._settings = settings

    async def _listing(self, store: S3ObjectStore, prefix: str) -> dict[str, ObjectSummary]:
        result = {}
        async for page in object_pages(store, prefix):
            for item in page.objects:
                if not item.key.startswith(prefix):
                    raise StorageError("backend_error", provider_code="WorkspacePrefixEscape")
                result[item.key[len(prefix):]] = item
        return result

    async def download(self, prefix: str, target: Path) -> None:
        prefix = _prefix(prefix)
        target.mkdir(parents=True, exist_ok=True)
        local = await _local(lambda: _files(target))
        async with S3ObjectStore.from_settings(self._settings) as store:
            snapshot = {name: item for name, item in (await self._listing(store, prefix)).items()
                        if name and not name.endswith("/")}
            for name in snapshot:
                _path(target, name)
                if any(parent.as_posix() in snapshot for parent in PurePosixPath(name).parents):
                    raise ValueError("Conflicting workspace file and directory paths")
            with TemporaryDirectory(prefix=".bifrost-s3-", dir=target) as temporary:
                staging = Path(temporary)
                if any(PurePosixPath(name).parts[0] == staging.name for name in snapshot):
                    raise ValueError("Workspace object collides with download staging")
                retained: set[str] = set()
                semaphore = asyncio.Semaphore(8)

                async def transfer(name: str, item: ObjectSummary) -> None:
                    async with semaphore:
                        if name in local and await _local(lambda: _matches(local[name], item)):
                            retained.add(name)
                            return
                        path = _path(staging, name)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        async with store.open_read(prefix + name, expected_version=item.version) as read:
                            with path.open("wb") as output:
                                async for chunk in read.body.iter_chunks():
                                    await _local(lambda: output.write(chunk))
                        if item.modified_at is not None:
                            timestamp = item.modified_at.timestamp()
                            os.utime(path, (timestamp, timestamp))

                await _transfers(transfer(name, item) for name, item in snapshot.items())
                for name, path in local.items():
                    if name not in snapshot:
                        path.unlink()
                for path in sorted(target.rglob("*"), key=lambda value: len(value.parts), reverse=True):
                    if path == staging or staging in path.parents:
                        continue
                    if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
                        path.rmdir()
                for name in snapshot:
                    if name in retained:
                        continue
                    destination = _path(target, name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # Preserve S3's existing follow-link behavior on download.
                    if destination.is_symlink():
                        destination = destination.resolve()
                    (staging / name).replace(destination)

    async def upload(self, source: Path, prefix: str, *, validate_snapshot: SnapshotGuard | None = None) -> None:
        prefix = _prefix(prefix)
        if not source.is_dir():
            raise ValueError("Workspace source directory does not exist")
        local = await _local(lambda: _files(source))
        async with S3ObjectStore.from_settings(self._settings) as store:
            snapshot = {name: item for name, item in (await self._listing(store, prefix)).items()
                        if name and not name.endswith("/")}
            if validate_snapshot is not None:
                validate_snapshot({name: item.version for name, item in snapshot.items()})
            semaphore = asyncio.Semaphore(8)

            async def transfer(name: str, path: Path) -> None:
                async with semaphore:
                    previous = snapshot.get(name)
                    if previous is not None and await _local(lambda: _matches(path, previous)):
                        return

                    async def chunks() -> AsyncIterator[bytes]:
                        with path.open("rb") as content:
                            while chunk := await _local(lambda: content.read(1024 * 1024)):
                                yield chunk

                    content_type, _ = mimetypes.guess_type(name)
                    async with aclosing(chunks()) as content:
                        await store.write_stream(
                            prefix + name, content,
                            options=WriteOptions(properties=ContentProperties(content_type=content_type)),
                        )

            await _transfers(transfer(name, path) for name, path in local.items())
            for name in snapshot:
                if name not in local:
                    await store.delete(prefix + name)

    async def delete(self, prefix: str) -> None:
        prefix = _prefix(prefix)
        async with S3ObjectStore.from_settings(self._settings) as store:
            for name in await self._listing(store, prefix):
                await store.delete(prefix + name)
