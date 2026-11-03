"""Exercise the opt-in streaming copy against the configured real test bucket."""
import hashlib
from uuid import uuid4

from src.config import get_settings
from src.services.file_storage.s3_client import S3StorageClient


async def test_stream_copy_preserves_bytes_headers_and_metadata():
    settings = get_settings().model_copy(update={"s3_multipart_copy_mode": "stream"})
    store = S3StorageClient(settings)
    prefix = f"test-stream-copy-{uuid4().hex}/"
    source_key, destination_key = prefix + "source", prefix + "destination"
    data = b"a" * (5 * 1024**2) + b"b" * 1024**2
    async with store.get_client() as client:
        try:
            await client.put_object(
                Bucket=settings.s3_bucket, Key=source_key, Body=data,
                ContentType="application/octet-stream", ContentDisposition="attachment",
                CacheControl="private, max-age=60", Expires="Wed, 01 Jan 2031 00:00:00 GMT",
                Metadata={"source": "original"},
            )
            head = await store.head_object(source_key)
            assert head is not None
            await store.copy_object(
                source_key, destination_key, source=head, metadata={"copy": "stream"},
                multipart_threshold=1, part_size=5 * 1024**2,
            )
            copied = await client.get_object(Bucket=settings.s3_bucket, Key=destination_key)
            async with copied["Body"] as body:
                assert hashlib.sha256(await body.read()).digest() == hashlib.sha256(data).digest()
            destination = await store.head_object(destination_key)
            assert destination is not None and destination.metadata == {"copy": "stream"}
            assert destination.headers["ContentType"] == "application/octet-stream"
            assert destination.headers["ContentDisposition"] == "attachment"
            assert destination.headers["CacheControl"] == "private, max-age=60"
            assert destination.headers["Expires"] == head.headers["Expires"]
        finally:
            for key in [source_key, destination_key]:
                await client.delete_object(Bucket=settings.s3_bucket, Key=key)
