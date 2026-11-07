"""S3's explicit streaming-copy mode preserves native content properties."""
import hashlib
from uuid import uuid4

import pytest

from src.config import get_settings
from src.core.storage import object_store
from src.core.storage.contracts import ContentProperties, WriteOptions
from src.services.file_storage.object_storage import FileObjectStorage


async def test_stream_copy_preserves_bytes_headers_and_metadata():
    settings = get_settings().model_copy(update={"s3_multipart_copy_mode": "stream"})
    files = FileObjectStorage(settings)
    prefix = f"test-stream-copy-{uuid4().hex}/"
    source_key, destination_key = prefix + "source", prefix + "destination"
    data = b"a" * (8 * 1024**2) + b"b" * 1024**2
    async with object_store(settings) as store:
        try:
            await store.write(source_key, data, options=WriteOptions(
                properties=ContentProperties(content_type="application/octet-stream", content_disposition="attachment",
                                             cache_control="private, max-age=60", http_expires="Wed, 01 Jan 2031 00:00:00 GMT"),
                metadata={"source": "original"},
            ))
            head = await files.head_object(source_key)
            assert head is not None
            await files.copy_object(source_key, destination_key, source=head, metadata={"copy": "stream"})
            async with store.open_read(destination_key) as read:
                assert hashlib.sha256(await read.body.read()).digest() == hashlib.sha256(data).digest()
            destination = await files.head_object(destination_key)
            assert destination is not None and destination.metadata == {"copy": "stream"}
            assert destination.properties == head.properties
        finally:
            await store.delete_many([source_key, destination_key])


async def test_same_size_source_mutation_between_normal_stream_parts_aborts_owned_mpu(monkeypatch):
    import xml.etree.ElementTree as ET
    from src.core.storage.s3 import S3ObjectStore
    from src.core.storage.contracts import CopyConditionFailed, CopyOptions, StorageError
    settings = get_settings().model_copy(update={"s3_multipart_copy_mode": "stream"})
    prefix = f"test-stream-race-{uuid4().hex}/"
    source_key, destination = prefix + "source", prefix + "destination"
    content = b"a" * (9 * 1024**2)
    async with S3ObjectStore.from_settings(settings) as store:
        try:
            await store.write(source_key, content)
            source = await store.stat(source_key)
            native_get = store._transport.get_object
            native_abort = store._transport.abort_multipart_upload
            replaced, aborts, ranges = False, [], []
            async def get(**options):
                if options.get("Key") == source_key:
                    ranges.append(options.get("Range"))
                response = await native_get(**options)
                if options.get("Key") == source_key and options.get("Range", "").startswith("bytes=0-"):
                    native_read = response["Body"].read
                    async def read(amount=None):
                        nonlocal replaced
                        data = await native_read(amount)
                        if not replaced:
                            replaced = True
                            await store.write(source_key, b"b" * len(content))
                        return data
                    response["Body"].read = read
                return response
            async def abort(**options):
                aborts.append(options)
                return await native_abort(**options)
            monkeypatch.setattr(store._transport, "get_object", get)
            monkeypatch.setattr(store._transport, "abort_multipart_upload", abort)
            with pytest.raises(CopyConditionFailed) as caught:
                await store.copy(source, destination, options=CopyOptions(create_only=True))
            assert caught.value.side == "source" and replaced
            assert ranges == ["bytes=0-8388607", "bytes=8388608-9437183"]
            assert len(aborts) == 1 and aborts[0]["Key"] == destination
            with pytest.raises(StorageError) as absent:
                await store.stat(destination)
            assert absent.value.kind == "not_found"
            uploads = await store._transport._request("GET", bucket=settings.s3_bucket, query=(("uploads", ""), ("prefix", prefix)))
            assert not ET.fromstring(uploads.content).findall(".//{*}Upload")
        finally:
            await store.delete_many([source_key, destination])
