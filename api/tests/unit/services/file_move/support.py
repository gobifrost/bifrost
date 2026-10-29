"""Fakes and helpers for the files.move engine and job tests."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from shared.file_paths import resolve_s3_key
from src.core.database import get_db_context
from src.models.orm.file_metadata import FileMetadata
from src.services.file_storage.move import (
    MARK_ETAG,
    MARK_ROW,
    MARK_SOURCE,
    MoveTarget,
    source_marker,
)
from src.services.file_storage.s3_client import (
    COPY_OBJECT_LIMIT,
    ObjectHead,
    ObjectMissing,
    ObjectPreconditionFailed,
)

Hook = Callable[[str], Awaitable[None]]


@dataclass
class StoredObject:
    data: bytes
    metadata: dict[str, str]
    etag: str


class FakeStore:
    """In-memory stand-in for the S3StorageClient move primitives.

    ``before[op]`` / ``after[op]`` hooks receive the key and run around the
    operation, so tests can inject failures and concurrent writers.
    """

    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}
        self.before: dict[str, Hook] = {}
        self.after: dict[str, Hook] = {}
        self.calls: list[tuple[str, str]] = []
        self.copies: list[dict[str, Any]] = []
        self._version = 0

    def put(self, key: str, data: bytes = b"%PDF-1.7 test", metadata: dict[str, str] | None = None) -> str:
        self._version += 1
        etag = '"' + hashlib.md5(data + str(self._version).encode()).hexdigest() + '"'
        self.objects[key] = StoredObject(data, dict(metadata or {}), etag)
        return etag

    async def _run(self, hooks: dict[str, Hook], op: str, key: str) -> None:
        hook = hooks.get(op)
        if hook is not None:
            await hook(key)

    async def head_object(self, key: str) -> ObjectHead | None:
        self.calls.append(("head", key))
        await self._run(self.before, "head", key)
        obj = self.objects.get(key)
        if obj is None:
            return None
        return ObjectHead(
            size=len(obj.data),
            etag=obj.etag,
            metadata=dict(obj.metadata),
            headers={"ContentType": "application/pdf"},
        )

    async def copy_object(
        self,
        source_key: str,
        destination_key: str,
        *,
        source: ObjectHead,
        metadata: dict[str, str],
        multipart_threshold: int = COPY_OBJECT_LIMIT,
        part_size: int | None = None,
    ) -> None:
        self.calls.append(("copy", destination_key))
        self.copies.append(
            {
                "source_key": source_key,
                "destination_key": destination_key,
                "source": source,
                "metadata": dict(metadata),
                "multipart_threshold": multipart_threshold,
                "part_size": part_size,
            }
        )
        await self._run(self.before, "copy", destination_key)
        current = self.objects.get(source_key)
        if current is None:
            raise ObjectMissing(source_key)
        if current.etag != source.etag:
            raise ObjectPreconditionFailed("source")
        self.put(destination_key, current.data, metadata)
        await self._run(self.after, "copy", destination_key)

    async def delete_object_strict(self, key: str) -> None:
        self.calls.append(("delete", key))
        await self._run(self.before, "delete", key)
        self.objects.pop(key, None)
        await self._run(self.after, "delete", key)

    async def list_keys(self, prefix: str, *, delimiter: str = "/") -> list[str]:
        self.calls.append(("list", prefix))
        return sorted(
            key
            for key in self.objects
            if key.startswith(prefix) and delimiter not in key[len(prefix):]
        )


def holds_move_copy(store: FakeStore, key: str) -> bool:
    """A marked move copy currently sits at ``key``."""
    obj = store.objects.get(key)
    return obj is not None and MARK_SOURCE in obj.metadata


def key_for(target: MoveTarget, path: str) -> str:
    return resolve_s3_key(target.location, target.scope, path)


def markers(source_key: str, row_id: UUID | None, etag: str) -> dict[str, str]:
    """The metadata an interrupted move's copy carries."""
    return {
        MARK_SOURCE: source_marker(source_key),
        MARK_ROW: str(row_id) if row_id is not None else "",
        MARK_ETAG: etag,
    }


async def seed_file(
    store: FakeStore,
    target: MoveTarget,
    path: str,
    data: bytes = b"%PDF-1.7 test",
    *,
    with_row: bool = True,
    metadata: dict[str, str] | None = None,
) -> UUID | None:
    """Put an object and (by default) its metadata row; returns the row id."""
    key = key_for(target, path)
    store.put(key, data, metadata)
    if not with_row:
        return None
    row_id = uuid4()
    async with get_db_context() as db:
        await db.execute(
            insert(FileMetadata).values(
                id=row_id,
                organization_id=target.organization_id,
                solution_id=target.solution_id,
                location=target.location,
                path=path,
                s3_key=key,
                content_type="application/pdf",
                size_bytes=len(data),
                created_by=uuid4(),
            )
        )
    return row_id


async def rows_at(target: MoveTarget, path: str) -> list[FileMetadata]:
    filters: list[Any] = [FileMetadata.location == target.location, FileMetadata.path == path]
    if target.solution_id is not None:
        filters.append(FileMetadata.solution_id == target.solution_id)
    else:
        filters += [
            FileMetadata.organization_id == target.organization_id,
            FileMetadata.solution_id.is_(None),
        ]
    async with get_db_context() as db:
        return list((await db.execute(select(FileMetadata).where(*filters))).scalars().all())


async def allow_rename(_path: str) -> bool:
    """``authorize_rename`` for tests that do not exercise rename policy."""
    return True
