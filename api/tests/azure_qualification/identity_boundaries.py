"""Explicit Azure-hosted identity authorization, SAS and race qualification.

Uses two task-owned accounts: the runner identity has a data role only on the
positive account. No role, account or container is created by this probe.
Only status summaries are printed, never tokens or signed URLs.
"""

import asyncio
import json
import os
from uuid import uuid4

import httpx

from src.config import Settings
from src.core.azure_blob_transport import AzureBlobTransport
from src.core.s3_transport import S3Error


def configuration(url):
    return Settings(
        _env_file=None,
        secret_key="qualification-only-unused-key-000000000000",
        object_storage_provider="azure_blob",
        azure_blob_account_url=url,
        azure_blob_container="adapter-qualification",
        azure_blob_auth="managed_identity",
        azure_blob_account_key=None,
        s3_bucket=None, s3_access_key=None, s3_secret_key=None,
    )


async def main():
    denied = configuration(os.environ["QUAL_DENIED_ACCOUNT_URL"])
    async with AzureBlobTransport(denied) as client:
        for name, operation in (
            ("rbac_denies_container_read", lambda: client.head_bucket(Bucket=denied.storage_bucket)),
            ("rbac_denies_delegation_key", lambda: client.generate_presigned_url("get_object", Params={"Bucket": denied.storage_bucket, "Key": "never-written"})),
        ):
            try:
                await operation()
            except S3Error as error:
                assert error.status_code == 403 and error.code == "AccessDenied"
            else:
                raise AssertionError(f"{name}: operation unexpectedly authorized")
            print(json.dumps({"passed": name}), flush=True)

    settings = configuration(os.environ["QUAL_ACCOUNT_URL"])
    prefix = f"_adapter_qualification/{uuid4().hex}/"
    async with AzureBlobTransport(settings) as first, AzureBlobTransport(settings) as second:
        bucket = settings.storage_bucket
        try:
            await first.put_object(Bucket=bucket, Key=prefix + "signed", Body=b"signed-fixture")
            longest = await first.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": prefix + "signed"}, ExpiresIn=604800)
            async with httpx.AsyncClient() as http:
                response = await http.get(longest)
                assert response.status_code == 200 and response.content == b"signed-fixture"
                shortest = await first.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": prefix + "signed"}, ExpiresIn=1)
                # Real service expiry is the boundary under test; this wait is
                # intentional, not a retry or a timing fix for another failure.
                await asyncio.sleep(3)
                assert (await http.get(shortest)).status_code == 403
            print(json.dumps({"passed": "delegation_sas_seven_day_and_expired_boundaries"}), flush=True)

            key = prefix + "race"
            uploads = []
            for client, data in ((first, b"first"), (second, b"second")):
                upload = await client.create_multipart_upload(Bucket=bucket, Key=key)
                part = await client.upload_part(Bucket=bucket, Key=key, UploadId=upload["UploadId"], PartNumber=1, Body=data)
                uploads.append((client, upload["UploadId"], part["ETag"]))
            results = await asyncio.gather(*(
                client.complete_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": etag}]}, IfNoneMatch="*")
                for client, upload_id, etag in uploads
            ), return_exceptions=True)
            assert sum(isinstance(result, dict) for result in results) == 1
            failures = [result for result in results if isinstance(result, BaseException)]
            assert len(failures) == 1 and isinstance(failures[0], S3Error) and failures[0].status_code == 412
            response = await first.get_object(Bucket=bucket, Key=key)
            async with response["Body"] as body:
                assert await body.read() in (b"first", b"second")
            for (client, upload_id, _), result in zip(uploads, results):
                if isinstance(result, BaseException):
                    await client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
            print(json.dumps({"passed": "independent_conditional_completion_has_one_winner"}), flush=True)
        finally:
            async for page in first.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
                for item in page["Contents"]:
                    await first.delete_object(Bucket=bucket, Key=item["Key"])
            assert not (await first.list_objects_v2(Bucket=bucket, Prefix=prefix))["Contents"]
            print(json.dumps({"fixture_cleanup": "verified"}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
