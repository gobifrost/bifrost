"""An emulator limitation probe, deliberately separate from success contracts.

A passing 501 assertion proves missing emulator capability, never copy parity.
"""

import pytest
from azure.core.credentials import AzureNamedKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.storage.blob.aio import BlobServiceClient



@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_pinned_azurite_does_not_implement_put_block_from_url(backend):
    settings, client, prefix = backend
    source, destination = prefix + "copy-source", prefix + "copy-destination"
    await client.put_object(Bucket=settings.storage_bucket, Key=source, Body=b"source")
    source_url = await client.generate_presigned_url(
        "get_object", Params={"Bucket": settings.storage_bucket, "Key": source}
    )
    async with BlobServiceClient(
        settings.azure_blob_account_url,
        credential=AzureNamedKeyCredential(
            settings.azure_blob_account_name,
            settings.azure_blob_account_key.get_secret_value(),
        ),
        retry_total=0,
    ) as service:
        blob = service.get_blob_client(settings.azure_blob_container, destination)
        with pytest.raises(HttpResponseError) as raised:
            await blob.stage_block_from_url(
                "capability-probe", source_url, source_offset=0, source_length=6
            )
        assert raised.value.status_code == 501
