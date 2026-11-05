from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest


class GuardedReader:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)
        self.sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "source artifact uploads must not buffer with read(-1)"
        self.sizes.append(size)
        return next(self._chunks)


class GuardedPath:
    def __init__(self, reader: GuardedReader) -> None:
        self.reader = reader

    def open(self, mode: str):
        assert mode == "rb"
        return self.reader


@pytest.mark.asyncio
async def test_write_from_path_streams_bounded_bytes_through_shared_storage(monkeypatch) -> None:
    from src.services.solutions import source_artifact

    reader = GuardedReader([b"first", b"second", b""])
    uploaded: list[bytes] = []

    class SharedStorage:
        def __init__(self, settings) -> None:
            assert settings.s3_bucket == "bucket"

        async def put_object_from_chunks(self, path, chunks, *, content_type):
            assert path.endswith("/source.zip")
            assert content_type == "application/zip"
            async for chunk in chunks:
                assert isinstance(chunk, bytes)
                uploaded.append(chunk)
            return "digest", sum(map(len, uploaded))

    class DirectTransport:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            raise AssertionError("write_from_path must use shared chunk storage")

        async def __aexit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(source_artifact, "S3StorageClient", SharedStorage)
    monkeypatch.setattr(source_artifact, "S3Transport", DirectTransport)
    settings = SimpleNamespace(
        s3_bucket="bucket",
        s3_endpoint_url="http://s3.test",
        s3_access_key="access",
        s3_secret_key="secret",
        s3_region="us-east-1",
    )
    storage = source_artifact.SolutionSourceArtifactStorage(uuid4(), settings)

    await storage.write_from_path(GuardedPath(reader))

    assert uploaded == [b"first", b"second"]
    assert reader.sizes == [source_artifact.CHUNK_SIZE] * 3
