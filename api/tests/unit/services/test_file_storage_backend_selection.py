from unittest.mock import MagicMock

from src.config import Settings
from src.services.file_storage.azure_blob_client import AzureBlobStorageClient
from src.services.file_storage.s3_client import S3StorageClient
from src.services.file_storage.service import FileStorageService


def _settings(**overrides):
    values = {
        "secret_key": "x" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def test_file_storage_uses_s3_backend_by_default():
    service = FileStorageService(db=MagicMock(), settings=_settings())

    assert isinstance(service._s3_storage, S3StorageClient)


def test_file_storage_uses_azure_blob_backend_when_configured():
    service = FileStorageService(
        db=MagicMock(),
        settings=_settings(
            object_storage_provider="azure_blob",
            azure_blob_account_url="https://example.blob.core.windows.net",
            azure_blob_container="bifrost-objects",
            azure_blob_auth="default_credential",
        ),
    )

    assert isinstance(service._s3_storage, AzureBlobStorageClient)
    assert service.presigned_upload_headers("text/plain") == {
        "Content-Type": "text/plain",
        "x-ms-blob-type": "BlockBlob",
    }
