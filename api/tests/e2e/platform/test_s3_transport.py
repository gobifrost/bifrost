"""Exercise the shared S3 wire contract against the real local object store."""

import hashlib
from uuid import uuid4

import httpx
import pytest

from src.config import get_settings
from src.core.s3_transport import S3Transport


@pytest.mark.e2e
async def test_s3_transport_preserves_objects_across_wire_operations():
    """Storage operations preserve bytes, metadata, keys and browser URLs."""
    settings = get_settings()
    bucket = settings.s3_bucket
    prefix = f"tests/s3-transport/{uuid4().hex}/"
    keys = {name: prefix + name for name in (
        "café/%2F/source.bin", "copied.bin", "uploaded.bin", "ranged.bin", "browser.bin", "report (1)+.pdf",
    )}
    source_key = keys["café/%2F/source.bin"]
    payload = b"wire-contract" * (5 * 1024 * 1024 // len(b"wire-contract") + 1)
    expected_digest = hashlib.sha256(payload).digest()
    active_uploads: dict[str, str] = {}

    async with S3Transport(
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key,
        secret_access_key=settings.s3_secret_key,
        region=settings.s3_region,
    ) as s3:
        try:
            await s3.head_bucket(Bucket=bucket)
            await s3.put_object(
                Bucket=bucket, Key=source_key, Body=payload,
                ContentType="application/octet-stream", Metadata={"recovery-marker": "fixture"},
            )
            source = await s3.head_object(Bucket=bucket, Key=source_key)
            assert source["ContentLength"] == len(payload)
            assert source["Metadata"] == {"recovery-marker": "fixture"}

            await s3.put_object(Bucket=bucket, Key=keys["report (1)+.pdf"], Body=b"rename candidate")
            candidates = await s3.list_objects_v2(Bucket=bucket, Prefix=prefix + "report (", Delimiter="/")
            assert [item["Key"] for item in candidates["Contents"]] == [keys["report (1)+.pdf"]]

            await s3.copy_object(
                Bucket=bucket, Key=keys["copied.bin"],
                CopySource={"Bucket": bucket, "Key": source_key},
                CopySourceIfMatch=source["ETag"], MetadataDirective="REPLACE",
                Metadata={"recovery-marker": "copied"}, ContentType="application/octet-stream",
            )
            copied = await s3.head_object(Bucket=bucket, Key=keys["copied.bin"])
            assert copied["Metadata"] == {"recovery-marker": "copied"}

            for target, server_copy in (("uploaded.bin", False), ("ranged.bin", True)):
                key = keys[target]
                upload = await s3.create_multipart_upload(Bucket=bucket, Key=key)
                upload_id = upload["UploadId"]
                active_uploads[key] = upload_id
                if server_copy:
                    part = await s3.upload_part_copy(
                        Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=1,
                        CopySource={"Bucket": bucket, "Key": source_key},
                        CopySourceIfMatch=source["ETag"], CopySourceRange=f"bytes=0-{len(payload) - 1}",
                    )
                    etag = part["CopyPartResult"]["ETag"]
                else:
                    part = await s3.upload_part(
                        Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=1, Body=payload,
                    )
                    etag = part["ETag"]
                await s3.complete_multipart_upload(
                    Bucket=bucket, Key=key, UploadId=upload_id,
                    MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": etag}]}, IfNoneMatch="*",
                )
                del active_uploads[key]

            # A real ordinary client uses the URL without signer-specific headers.
            upload_url = await s3.generate_presigned_url(
                "put_object", Params={"Bucket": bucket, "Key": keys["browser.bin"], "ContentType": "text/plain"},
                ExpiresIn=60,
            )
            async with httpx.AsyncClient() as browser:
                uploaded = await browser.put(upload_url, content=b"browser fixture", headers={"Content-Type": "text/plain"})
                assert uploaded.status_code == 200, uploaded.text
                download_url = await s3.generate_presigned_url(
                    "get_object", Params={
                        "Bucket": bucket, "Key": keys["browser.bin"],
                        "ResponseContentType": "application/octet-stream",
                        "ResponseContentDisposition": 'attachment; filename="fixture.bin"',
                    }, ExpiresIn=60,
                )
                downloaded = await browser.get(download_url)
                assert downloaded.status_code == 200, downloaded.text
                assert downloaded.content == b"browser fixture"
                assert downloaded.headers["content-type"] == "application/octet-stream"
                assert downloaded.headers["content-disposition"] == 'attachment; filename="fixture.bin"'

            for key in (source_key, keys["copied.bin"], keys["uploaded.bin"], keys["ranged.bin"]):
                response = await s3.get_object(Bucket=bucket, Key=key)
                digest = hashlib.sha256()
                async with response["Body"] as body:
                    async for chunk in body.iter_chunks(64 * 1024):
                        assert len(chunk) <= 64 * 1024
                        digest.update(chunk)
                assert digest.digest() == expected_digest

            paginator = s3.get_paginator("list_objects_v2")
            pages = [page async for page in paginator.paginate(Bucket=bucket, Prefix=prefix, MaxKeys=1)]
            assert len(pages) == len(keys)
            assert {item["Key"] for page in pages for item in page["Contents"]} == set(keys.values())

            # An abandoned multipart upload is also caller-owned and abortable.
            abandoned_key = prefix + "abandoned.bin"
            abandoned = await s3.create_multipart_upload(Bucket=bucket, Key=abandoned_key)
            active_uploads[abandoned_key] = abandoned["UploadId"]
            await s3.abort_multipart_upload(Bucket=bucket, Key=abandoned_key, UploadId=active_uploads[abandoned_key])
            del active_uploads[abandoned_key]
        finally:
            for key, upload_id in active_uploads.items():
                await s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
            deleted = await s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": key} for key in keys.values()]})
            assert not deleted["Errors"], deleted
