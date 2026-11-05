"""Select one of Bifrost's two object transports; S3 remains the default."""

from typing import Any

from src.config import Settings
from src.core.s3_transport import S3Transport


def storage_transport(settings: Settings, *, s3_factory: Any = S3Transport) -> Any:
    if settings.object_storage_provider == "azure_blob":
        from src.core.azure_blob_transport import AzureBlobTransport

        return AzureBlobTransport(settings)
    return s3_factory(
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key,
        secret_access_key=settings.s3_secret_key,
        region=settings.s3_region,
    )
