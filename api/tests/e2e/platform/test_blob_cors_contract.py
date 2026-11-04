"""Local emulator HTTP CORS/SAS checks, not real-Azure browser acceptance."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from azure.core.credentials import AzureNamedKeyCredential
from azure.storage.blob import BlobSasPermissions, CorsRule, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from src.services.file_storage.s3_client import S3StorageClient


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_azurite_signed_upload_cors_and_expired_sas(backend):
    settings, client, prefix = backend
    origin = "https://fixture.example.invalid"
    key = prefix + "browser-upload"
    async with BlobServiceClient(
        settings.azure_blob_account_url,
        credential=AzureNamedKeyCredential(
            settings.azure_blob_account_name,
            settings.azure_blob_account_key.get_secret_value(),
        ),
    ) as service:
        old = await service.get_service_properties()
        try:
            await service.set_service_properties(
                cors=[
                    CorsRule(
                        allowed_origins=[origin],
                        allowed_methods=["GET", "HEAD", "PUT"],
                        allowed_headers=["content-type", "x-ms-blob-type"],
                        exposed_headers=["ETag", "Content-Disposition"],
                        max_age_in_seconds=60,
                    )
                ]
            )
            url = await client.generate_presigned_url(
                "put_object",
                Params={"Bucket": settings.storage_bucket, "Key": key},
                ExpiresIn=60,
            )
            async with httpx.AsyncClient() as http:
                preflight = await http.options(
                    url,
                    headers={
                        "Origin": origin,
                        "Access-Control-Request-Method": "PUT",
                        "Access-Control-Request-Headers": "content-type,x-ms-blob-type",
                    },
                )
                assert preflight.status_code == 200
                assert preflight.headers["access-control-allow-origin"] == origin
                headers = S3StorageClient(settings).presigned_upload_headers(
                    "text/plain"
                )
                uploaded = await http.put(
                    url,
                    content=b"browser-fixture",
                    headers={"Origin": origin, **headers},
                )
                assert uploaded.status_code == 201
                assert uploaded.headers["access-control-allow-origin"] == origin
                assert (
                    "etag" in uploaded.headers["access-control-expose-headers"].lower()
                )
                denied = await http.options(
                    url,
                    headers={
                        "Origin": "https://denied.example.invalid",
                        "Access-Control-Request-Method": "PUT",
                    },
                )
                assert "access-control-allow-origin" not in denied.headers
                signature = generate_blob_sas(
                    account_name=settings.azure_blob_account_name,
                    container_name=settings.storage_bucket,
                    blob_name=key,
                    account_key=settings.azure_blob_account_key.get_secret_value(),
                    permission=BlobSasPermissions(read=True),
                    expiry=datetime.now(UTC) - timedelta(minutes=1),
                )
                download_url = service.get_blob_client(settings.storage_bucket, key).url
                expired = await http.get(download_url + "?" + signature)
                assert expired.status_code == 403
            response = await client.get_object(Bucket=settings.storage_bucket, Key=key)
            assert await response["Body"].read() == b"browser-fixture"
        finally:
            await service.set_service_properties(cors=old["cors"])
