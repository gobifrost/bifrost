from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import hashlib

from src.core.storage.contracts import ContentProperties, ObjectInfo, ObjectPage, ObjectSummary, ObjectVersion, PageCursor, StorageError, StreamWriteResult
from uuid import uuid4

import pytest


class MemoryBody:
    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class MemoryStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.page_size: int | None = None
        self.seen_tokens: list[str | None] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def write_stream(self, key, chunks, *, options):
        assert options.properties.content_type == "application/zip"
        data = b"".join([chunk async for chunk in chunks])
        self.objects[key] = data
        return StreamWriteResult(ObjectVersion("fixture"), len(data), hashlib.sha256(data).hexdigest())

    async def stat(self, key):
        if key not in self.objects:
            raise StorageError("not_found", provider_code="fixture")
        return ObjectInfo(key, len(self.objects[key]), ObjectVersion("fixture"), None, ContentProperties(), {}, None)

    @asynccontextmanager
    async def open_read(self, key):
        info = await self.stat(key)
        data = self.objects[key]
        class Body(MemoryBody):
            async def iter_chunks(self, chunk_size):
                for offset in range(0, len(data), chunk_size):
                    yield data[offset:offset + chunk_size]
        yield SimpleNamespace(info=info, body=Body(data))

    async def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    async def delete_many(self, keys):
        for key in keys:
            await self.delete(key)

    async def list_page(self, prefix, *, cursor=None, **_kwargs):
        self.seen_tokens.append(cursor)
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        offset = int(cursor) if cursor else 0
        size = self.page_size or len(keys)
        page = keys[offset:offset + size]
        following = offset + size
        return ObjectPage(tuple(ObjectSummary(key, len(self.objects[key]), ObjectVersion("fixture"), None)
                                for key in page), (), PageCursor(str(following)) if following < len(keys) else None)


@pytest.fixture
def source_storage(monkeypatch: pytest.MonkeyPatch):
    from src.services import application_source_artifact

    memory = MemoryStore()
    monkeypatch.setattr(application_source_artifact, "object_store", lambda settings: memory)
    return application_source_artifact.ApplicationSourceArtifactStorage(), memory


@pytest.mark.asyncio
async def test_source_artifact_writes_reads_and_deletes_exact_deployment_key(
    tmp_path: Path, source_storage
) -> None:
    storage, memory = source_storage
    app_id = uuid4()
    deployment_id = uuid4()
    source = tmp_path / "source.zip"
    source.write_bytes(b"zip-bytes")

    digest, size = await storage.write_deployment_source(app_id, deployment_id, source)

    key = f"_application_artifacts/{app_id}/deployments/{deployment_id}/source.zip"
    assert memory.objects == {key: b"zip-bytes"}
    assert digest == hashlib.sha256(b"zip-bytes").hexdigest()
    assert size == len(b"zip-bytes")

    copied = tmp_path / "copied.zip"
    assert await storage.copy_deployment_source_to_path(app_id, deployment_id, copied) == len(
        b"zip-bytes"
    )
    assert copied.read_bytes() == b"zip-bytes"

    assert await storage.read_deployment_source(app_id, deployment_id) == b"zip-bytes"
    assert await storage.deployment_source_exists(app_id, deployment_id) is True
    assert b"".join(
        [chunk async for chunk in storage.iter_deployment_source(app_id, deployment_id)]
    ) == b"zip-bytes"

    await storage.delete_deployment_source(app_id, deployment_id)
    assert memory.deleted == [key]
    assert memory.objects == {}


@pytest.mark.asyncio
async def test_source_artifact_deletes_all_artifacts_for_app_prefix(source_storage) -> None:
    storage, memory = source_storage
    app_id = uuid4()
    other_app_id = uuid4()
    first = uuid4()
    second = uuid4()
    memory.objects = {
        f"_application_artifacts/{app_id}/deployments/{first}/source.zip": b"1",
        f"_application_artifacts/{app_id}/deployments/{second}/source.zip": b"2",
        f"_application_artifacts/{other_app_id}/deployments/{uuid4()}/source.zip": b"3",
    }

    await storage.delete_application_artifacts(app_id)

    assert sorted(memory.objects) == [
        next(key for key in memory.objects if key.startswith(f"_application_artifacts/{other_app_id}/"))
    ]
    assert sorted(memory.deleted) == sorted(
        [
        f"_application_artifacts/{app_id}/deployments/{first}/source.zip",
        f"_application_artifacts/{app_id}/deployments/{second}/source.zip",
        ]
    )


@pytest.mark.asyncio
async def test_source_artifact_delete_application_artifacts_paginates(source_storage) -> None:
    storage, memory = source_storage
    app_id = uuid4()
    retained_keys = [
        f"_application_artifacts/{app_id}/deployments/{uuid4()}/source.zip"
        for _ in range(3)
    ]
    memory.objects = {key: str(index).encode() for index, key in enumerate(retained_keys)}
    memory.page_size = 1

    await storage.delete_application_artifacts(app_id)

    assert memory.objects == {}
    assert sorted(memory.deleted) == sorted(retained_keys)
    assert memory.seen_tokens == [None, "1", "2"]
